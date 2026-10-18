"""Gated competing writers and independent readers over a real filesystem.

The controller releases concrete partial-write and rename boundaries. Timeouts
are liveness bounds only; elapsed time never chooses a schedule or an expected
result. The model concerns atomic visibility, not power-loss persistence or a
transaction spanning separate files. Run each history in an outer worker.
"""

from __future__ import annotations

from contextlib import suppress
import errno
import hashlib
import multiprocessing
import os
from pathlib import Path
from queue import Queue
import stat
import threading
from typing import TYPE_CHECKING, Any

from unstd import fs


if TYPE_CHECKING:
    from collections.abc import Callable


_WAIT = 8
_CURRENT = threading.local()
_REAL_REPLACE = Path.replace
_INITIAL = b"initial generation\x00\xff" * 257


class _CancelledError(Exception):
    """A controller teardown releases a gated actor and rolls back its body."""


def _digest(value: bytes | None) -> str | None:
    return None if value is None else hashlib.sha256(value).hexdigest()


def _payload(name: str, mode: str) -> str | bytes:
    if mode == "text":
        return (f"{name}: caf\u00e9 \u03bb \U0001f30d\r\n" * 4097) + f"end:{name}"
    return name.encode() + b"\x00" + bytes(range(256)) * 257 + name.encode()


def _wire(value: str | bytes) -> bytes:
    return (
        value.replace("\n", os.linesep).encode("utf-8")
        if isinstance(value, str)
        else value
    )


class _ThreadChannel:
    def __init__(self, incoming: Queue, outgoing: Queue) -> None:
        self.incoming = incoming
        self.outgoing = outgoing

    def send(self, value: Any) -> None:
        self.outgoing.put(value, timeout=_WAIT)

    def recv(self) -> Any:
        return self.incoming.get(timeout=_WAIT)


class _PipeChannel:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def send(self, value: Any) -> None:
        self.connection.send(value)

    def recv(self) -> Any:
        assert self.connection.poll(_WAIT), "actor did not reach the scheduled boundary"
        return self.connection.recv()


def _replace(path: Path, target: Path) -> Path:
    """Pause only the current writer's real product rename operation."""
    state = getattr(_CURRENT, "writer", None)
    if state is None or Path(target) != state["destination"]:
        return _REAL_REPLACE(path, target)
    channel = state["channel"]
    descriptor_released = False
    try:
        info = os.fstat(state["descriptor"])
        # Another actor may reuse the integer immediately after close. Check
        # ownership of this inode, rather than treating a reused FD as a leak.
        descriptor_released = (info.st_dev, info.st_ino) != state["inode"]
    except OSError as error:
        descriptor_released = error.errno == errno.EBADF
    channel.send(
        {
            "event": "before-replace",
            "actor": state["name"],
            "scratch": str(path),
            "scratch_bytes": path.read_bytes(),
            "descriptor_released": descriptor_released,
        }
    )
    command = channel.recv()
    if command == "cancel":
        raise _CancelledError
    if command == "fail-replace":
        raise OSError(errno.EACCES, "scheduled competing-writer replace failure")
    assert command == "commit", "writer received an unscheduled commit command"
    if state["mutation"] == "drop-commit":
        return Path(target)
    result = _REAL_REPLACE(path, target)
    if state["mutation"] == "tear-commit":
        contents = Path(target).read_bytes()
        Path(target).write_bytes(contents[: len(contents) // 2])
    return result


def _fail_body() -> None:
    raise OSError(errno.ENOSPC, "scheduled competing-writer body failure")


def _writer(channel: Any, spec: dict) -> None:
    name = spec["name"]
    destination = Path(spec["destination"])
    value = _payload(name, spec["mode"])
    middle = len(value) // 2
    calls: list[str] = []
    real_secrets = fs.secrets

    try:
        if spec["collision"]:
            # Process actors have private modules. Thread actors share this
            # facade, which dispatches scratch identities by current writer.
            _CURRENT.names = calls
        with fs.atomic_writer(
            destination,
            mode="w" if spec["mode"] == "text" else "wb",
            durable=spec["durable"],
        ) as stream:
            _CURRENT.writer = {
                "name": name,
                "destination": destination,
                "channel": channel,
                "descriptor": stream.fileno(),
                "mutation": spec["mutation"],
            }
            stream.write(value[:middle])
            stream.flush()
            info = os.fstat(stream.fileno())
            _CURRENT.writer["inode"] = (info.st_dev, info.st_ino)
            channel.send(
                {
                    "event": "partial",
                    "actor": name,
                    "inode": (info.st_dev, info.st_ino),
                    "position": stream.tell(),
                    "mode": stat.S_IMODE(info.st_mode),
                    "name_calls": list(calls),
                }
            )
            command = channel.recv()
            if command == "cancel":
                raise _CancelledError  # noqa: TRY301 - cancel inside the tested context
            if command == "fail-body":
                _fail_body()
            assert command == "finish", "writer body received an unscheduled command"
            stream.write(value[middle:])
        channel.send({"event": "committed", "actor": name})
    except _CancelledError:
        channel.send({"event": "cancelled", "actor": name})
    except OSError as error:
        channel.send({"event": "failed", "actor": name, "errno": error.errno})
    except BaseException as error:
        channel.send({"event": "unexpected", "actor": name, "error": repr(error)})
        raise
    finally:
        _CURRENT.writer = None
        if spec["collision"]:
            fs.secrets = real_secrets


def _reader(channel: Any, spec: dict) -> None:
    destination = Path(spec["destination"])
    retained = destination.open("rb") if spec["retained"] else None
    pinned = None
    head = b""
    try:
        channel.send({"event": "ready", "actor": spec["name"]})
        while True:
            command = channel.recv()
            if command == "stop":
                channel.send({"event": "stopped", "actor": spec["name"]})
                return
            if command == "pin":
                assert pinned is None, "reader leaked its prior pinned descriptor"
                try:
                    pinned = destination.open("rb")
                    head = pinned.read(17)
                    info = os.fstat(pinned.fileno())
                    inode = (info.st_dev, info.st_ino)
                except FileNotFoundError:
                    head = inode = None
                channel.send(
                    {
                        "event": "pinned",
                        "actor": spec["name"],
                        "head": head,
                        "inode": inode,
                    }
                )
                continue
            if command == "unpin":
                contents = None if pinned is None else head + pinned.read()
                if pinned is not None:
                    pinned.close()
                    pinned = None
                channel.send(
                    {"event": "retired", "actor": spec["name"], "data": contents}
                )
                continue
            assert command == "read", "reader received an unscheduled command"
            try:
                with destination.open("rb") as stream:
                    contents = stream.read()
                    info = os.fstat(stream.fileno())
                    inode = (info.st_dev, info.st_ino)
            except FileNotFoundError:
                contents = inode = None
            old = None
            if retained is not None:
                retained.seek(0)
                old = retained.read()
            channel.send(
                {
                    "event": "observed",
                    "actor": spec["name"],
                    "data": contents,
                    "inode": inode,
                    "retained": old,
                }
            )
    finally:
        if pinned is not None:
            pinned.close()
        if retained is not None:
            retained.close()


def _actor_entry(connection: Any, role: str, spec: dict) -> None:
    """Importable spawn entry; no forked interpreter state is assumed."""
    Path.replace = _replace
    if spec.get("collision"):
        fs.secrets = _CollisionNames()
    try:
        {"writer": _writer, "reader": _reader}[role](_PipeChannel(connection), spec)
    finally:
        connection.close()


class _CollisionNames:
    @staticmethod
    def token_hex(width: int) -> str:
        assert width == 4
        calls = _CURRENT.names
        scratch_name = "shared" if not calls else threading.current_thread().name
        # Process thread names are identical; actors' PID makes retries private
        # without changing the forced first collision identity.
        if calls and scratch_name == "MainThread":
            scratch_name = f"retry-{os.getpid()}"
        calls.append(scratch_name)
        return scratch_name


class _Actor:
    def __init__(self, kind: str, role: str, spec: dict) -> None:
        self.kind = kind
        self.role = role
        self.name = spec["name"]
        self.received: list[str] = []
        if kind == "process":
            context = multiprocessing.get_context("spawn")
            connection, remote = context.Pipe()
            self.channel = _PipeChannel(connection)
            self.worker = context.Process(
                target=_actor_entry, args=(remote, role, spec), name=self.name
            )
            self.worker.start()
            remote.close()
        else:
            commands: Queue = Queue(maxsize=4)
            events: Queue = Queue(maxsize=4)
            self.channel = _ThreadChannel(events, commands)
            self.worker = threading.Thread(
                target={"writer": _writer, "reader": _reader}[role],
                args=(_ThreadChannel(commands, events), spec),
                name=self.name,
                daemon=True,
            )
            self.worker.start()

    def send(self, command: str) -> None:
        self.channel.send(command)

    def expect(self, event: str) -> dict:
        row = self.channel.recv()
        assert row["actor"] == self.name, "actor channels crossed"
        assert row["event"] == event, f"expected {event}, received {row}"
        self.received.append(event)
        return row

    def join(self) -> None:
        self.worker.join(timeout=_WAIT)
        assert not self.worker.is_alive(), "scheduled actor failed to terminate"
        if self.kind == "process":
            assert self.worker.exitcode == 0, "actor process exited unsuccessfully"

    def close(self) -> None:
        if self.worker.is_alive():
            if self.kind == "process":
                self.worker.kill()
            else:
                with suppress(Exception):
                    self.send("cancel" if self.role == "writer" else "stop")
            self.worker.join(timeout=_WAIT)
            assert not self.worker.is_alive(), "controller cleanup left a live actor"
        if self.kind == "process":
            self.channel.connection.close()
            self.worker.close()


def atomic_history(
    directory: Path,
    order: tuple[str, ...],
    *,
    actor_kind: str,
    mode: str = "binary",
    durable: bool = False,
    existing: bool = True,
    failure: str | None = None,
    collision: bool = False,
    record: Callable[[dict], None] | None = None,
    _mutation: str | None = None,
) -> dict:
    """Check an exact model through overlapping bodies, commits and rollbacks."""
    assert set(order) == {"alpha", "beta", "gamma"}
    assert len(order) == 3
    assert actor_kind in {"thread", "process"}
    assert mode in {"binary", "text"}
    assert failure in {None, "body", "replace"}
    assert _mutation in {None, "drop-commit", "tear-commit"}
    destination = directory / "state.bin"
    neighbor = directory / "neighbor.bin"
    neighbor.write_bytes(b"independent neighbor\x00\xff")
    if existing:
        destination.write_bytes(_INITIAL)
        if os.name == "posix":
            destination.chmod(0o640)
    committed = _INITIAL if existing else None
    initial_inode = (
        (destination.stat().st_dev, destination.stat().st_ino) if existing else None
    )
    expected_mode = stat.S_IMODE(destination.stat().st_mode) if existing else None
    retained = existing and os.name == "posix"
    events: list[dict] = []
    actors: list[_Actor] = []
    readers: list[_Actor] = []
    writers: dict[str, _Actor] = {}
    partials: dict[str, dict] = {}
    failed = order[1] if failure else None
    real_replace = Path.replace
    real_secrets = fs.secrets
    Path.replace = _replace
    if collision:
        fs.secrets = _CollisionNames()

    def evidence(event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        events.append(row)
        if record is not None:
            record(row)

    def observe(phase: str) -> None:
        for reader in readers:
            reader.send("read")
        for reader in readers:
            row = reader.expect("observed")
            assert row["data"] == committed, (
                "reader observed uncommitted or torn generation"
                if phase != "after-commit"
                else "committed generation not visible as a whole payload"
            )
            if retained:
                assert row["retained"] == _INITIAL, (
                    "old reader descriptor changed generation"
                )
            assert row["inode"] == current_inode, "reader opened an unexpected inode"
            if committed is not None and os.name == "posix":
                assert stat.S_IMODE(destination.stat().st_mode) == expected_mode, (
                    "competing writer changed the destination mode"
                )
            evidence(
                "reader",
                actor=reader.name,
                phase=phase,
                sha256=_digest(row["data"]),
                size=None if row["data"] is None else len(row["data"]),
                retained_sha256=_digest(row["retained"]),
            )

    current_inode = initial_inode
    try:
        for index in range(2):
            reader = _Actor(
                actor_kind,
                "reader",
                {
                    "name": f"reader-{index}",
                    "destination": str(destination),
                    "retained": retained,
                },
            )
            readers.append(reader)
            actors.append(reader)
            evidence("awaiting", actor=reader.name, checkpoint="reader-ready")
            reader.expect("ready")
        # Starting the collision winner first fixes the allocation schedule.
        # The second and third writers contend with a still-open scratch file.
        for name in order:
            actor = _Actor(
                actor_kind,
                "writer",
                {
                    "name": name,
                    "destination": str(destination),
                    "mode": mode,
                    "durable": durable,
                    "collision": collision,
                    "mutation": _mutation,
                },
            )
            actors.append(actor)
            writers[name] = actor
            evidence("awaiting", actor=name, checkpoint="partial-write")
            partials[name] = actor.expect("partial")
            middle = len(_payload(name, mode)) // 2
            assert partials[name]["position"] == len(
                _wire(_payload(name, mode)[:middle])
            )
            if expected_mode is None:
                expected_mode = partials[name]["mode"]
            assert partials[name]["mode"] == expected_mode
            evidence("partial", actor=name, name_calls=partials[name]["name_calls"])
        assert len({tuple(row["inode"]) for row in partials.values()}) == 3, (
            "competing writers share a scratch inode"
        )
        observe("all-partial")
        prepared: dict[str, dict] = {}
        for name in order:
            if name == failed and failure == "body":
                continue
            writers[name].send("finish")
        for name in order:
            if name == failed and failure == "body":
                continue
            evidence("awaiting", actor=name, checkpoint="before-replace")
            row = writers[name].expect("before-replace")
            assert row["descriptor_released"], (
                "scratch descriptor reached rename still owned"
            )
            assert row["scratch_bytes"] == _wire(_payload(name, mode)), (
                "independent writer handle lost or mixed its own payload"
            )
            assert Path(row["scratch"]).parent == directory
            prepared[name] = row
            evidence("prepared", actor=name, sha256=_digest(row["scratch_bytes"]))
        assert len({row["scratch"] for row in prepared.values()}) == len(prepared)
        observe("all-prepared")
        successful = [name for name in order if name != failed]
        for index, name in enumerate(successful):
            observe("before-commit")
            previous = committed
            if os.name == "posix":
                for reader in readers:
                    reader.send("pin")
                for reader in readers:
                    row = reader.expect("pinned")
                    assert row["head"] == (None if previous is None else previous[:17])
                    assert row["inode"] == current_inode
                    evidence(
                        "reader-pinned",
                        actor=reader.name,
                        generation_sha256=_digest(previous),
                    )
            evidence("releasing", actor=name, checkpoint="replace")
            writers[name].send("commit")
            writers[name].expect("committed")
            committed = _wire(_payload(name, mode))
            current_inode = tuple(partials[name]["inode"])
            evidence("commit", actor=name, sha256=_digest(committed))
            if os.name == "posix":
                for reader in readers:
                    reader.send("unpin")
                for reader in readers:
                    row = reader.expect("retired")
                    assert row["data"] == previous, (
                        "rename changed an in-progress reader's generation"
                    )
                    evidence(
                        "reader-retired", actor=reader.name, sha256=_digest(row["data"])
                    )
            observe("after-commit")
            if index == 0 and failed is not None:
                evidence("releasing", actor=failed, checkpoint=f"fail-{failure}")
                writers[failed].send(
                    "fail-body" if failure == "body" else "fail-replace"
                )
                error = writers[failed].expect("failed")
                assert error["errno"] == (
                    errno.ENOSPC if failure == "body" else errno.EACCES
                )
                evidence(
                    "rollback", actor=failed, boundary=failure, errno=error["errno"]
                )
                observe("after-rollback")
        for actor in writers.values():
            actor.join()
        for reader in readers:
            reader.send("stop")
        for reader in readers:
            reader.expect("stopped")
            reader.join()
        assert destination.read_bytes() == committed
        assert neighbor.read_bytes() == b"independent neighbor\x00\xff"
        assert sorted(path.name for path in directory.iterdir()) == [
            "neighbor.bin",
            "state.bin",
        ], "competing writer left a scratch file behind"
        if collision:
            assert partials[order[0]]["name_calls"] == ["shared"]
            assert all(len(partials[name]["name_calls"]) == 2 for name in order[1:])
        evidence(
            "joined", writers=3, readers=2, retained_descriptors=2 if retained else 0
        )
        return {
            "events": events,
            "commits": successful,
            "failed": failed,
            "final_sha256": _digest(committed),
            "retained_descriptors": 2 if retained else 0,
            "reader_count": sum(row["event"] == "reader" for row in events),
        }
    finally:
        # Release gated thread actors before restoring their shared dispatchers.
        for actor in reversed(actors):
            actor.close()
        Path.replace = real_replace
        fs.secrets = real_secrets
