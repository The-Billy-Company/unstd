"""Reviewed shared-object schedules, each run in a deadline-owned interpreter.

Only immutable snapshots, independent inputs and documented reusable objects are
shared. No scenario relies on the GIL to order an observation, or asks a mutable
container to support unsynchronised mutation. Barriers force the relevant cold
cache or callback overlap; native execution is contained by the calling process.
"""

from __future__ import annotations

import copy
import dataclasses
import io
import json
from queue import Queue
import re
import sys
from threading import Barrier, Thread, current_thread, local
import time
from typing import TYPE_CHECKING

import pytest

from tests.support.chaos.journal import ordered


if TYPE_CHECKING:
    from collections.abc import Callable


DEADLINE = 5
PARTIES = 4
_EVENTS: Queue[dict] = Queue()


def _event(event: str, **fields):
    _EVENTS.put({"event": event, "actor": current_thread().name, **fields})


class _Gate:
    """A named barrier with reached and released evidence for every actor."""

    def __init__(self, name: str, parties: int):
        self.name = name
        self.barrier = Barrier(parties, timeout=DEADLINE)
        _event("barrier.planned", barrier=name, parties=parties)

    def wait(self):
        _event("barrier.arrived", barrier=self.name)
        self.barrier.wait()
        _event("barrier.released", barrier=self.name)


def _parallel(actions: list[Callable[[], object]]) -> list[object]:
    """Join every worker against one deadline and propagate every failure."""
    start = _Gate("workers.start", len(actions))
    results: Queue[tuple[int, object, BaseException | None]] = Queue()

    def run(index, action):
        try:
            start.wait()
            _event("worker.started", index=index)
            value = action()
        except BaseException as error:
            _event("worker.failed", index=index, error_type=type(error).__name__)
            results.put((index, None, error))
        else:
            _event("worker.completed", index=index)
            results.put((index, value, None))

    threads = [
        Thread(
            target=run,
            args=(index, action),
            daemon=True,
            name=f"shared-state-{index}",
        )
        for index, action in enumerate(actions)
    ]
    deadline = time.monotonic() + DEADLINE
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    assert not any(thread.is_alive() for thread in threads), "worker thread deadline"
    _event("workers.joined", count=len(threads))
    assert results.qsize() == len(actions), "worker disappeared without an observation"
    rows = sorted((results.get_nowait() for _ in actions), key=lambda row: row[0])
    for _, _, error in rows:
        if error is not None:
            raise error
    return [value for _, value, _ in rows]


def _identities(rows):
    assert len({id(row) for row in rows}) == len(rows), "shared mutable output"


def _clone_admission(seed, *, broken=False):
    from unstd.clone import structural as clone

    @dataclasses.dataclass
    class Record:
        owner: str
        rows: list[dict[str, list[int]]]

    baseline = Record("shared", [{"values": [1, 2]}])
    expected = copy.deepcopy(baseline)
    gate = _Gate("clone.cold_admission", PARTIES)
    admit = clone._admit
    admissions = Queue()

    def simultaneous(cls):
        if cls is Record:
            admissions.put(cls.__name__)
            gate.wait()
        return admit(cls)

    clone._admit = simultaneous
    actors = ordered(seed, "concurrency.clone.admission", list(range(PARTIES)))
    outputs = _parallel([lambda: clone.deep(baseline) for _ in actors])
    if broken:
        outputs[1] = outputs[0]
    assert admissions.qsize() == PARTIES, "cold admission overlap did not occur"
    assert baseline == expected
    assert all(value == expected and value is not baseline for value in outputs)
    _identities(outputs)
    _identities([value.rows[0]["values"] for value in outputs])
    for index, value in enumerate(outputs):
        value.rows[0]["values"].append(index + 10)
    assert baseline == expected
    return {"actors": list(actors), "cold_admissions": admissions.qsize()}


def _clone_fields(seed, *, broken=False):
    from unstd.clone import structural as clone

    @dataclasses.dataclass
    class Parent:
        name: str

    @dataclasses.dataclass
    class Child(Parent):
        rows: list[dict[str, list[int]]]

    baseline = Child("inherited", [{"values": [1, 2]}])
    expected = dataclasses.asdict(baseline)
    gate = _Gate("clone.cold_fields", PARTIES)
    fields = dataclasses.fields
    discovered = Queue()

    def simultaneous(cls):
        if cls is Child:
            discovered.put(cls.__name__)
            gate.wait()
        return fields(cls)

    dataclasses.fields = simultaneous
    actors = ordered(seed, "concurrency.clone.fields", list(range(PARTIES)))
    outputs = _parallel([lambda: clone.asdict(baseline) for _ in actors])
    if broken:
        outputs[1]["rows"] = outputs[0]["rows"]
    assert discovered.qsize() == PARTIES, "cold field discovery did not overlap"
    assert all(value == expected for value in outputs)
    _identities(outputs)
    _identities([value["rows"][0]["values"] for value in outputs])
    outputs[0]["rows"][0]["values"].append(99)
    assert baseline.rows[0]["values"] == [1, 2]
    assert outputs[1] == expected
    return {"actors": list(actors), "field_discoveries": discovered.qsize()}


def _clone_reentry(seed, *, broken=False):
    from unstd.clone import structural as clone

    gate = _Gate("clone.copy_hook", PARTIES)
    calls = Queue()
    nested = {"inner": [7, 8]}

    class Foreign:
        def __deepcopy__(self, memo):
            calls.put(len(memo))
            gate.wait()
            return {"copied": clone.deep(nested)}

    foreign = Foreign()
    baseline = {"hook": foreign, "sibling": [1, 2]}
    actors = ordered(seed, "concurrency.clone.reentry", list(range(PARTIES)))
    outputs = _parallel([lambda: clone.deep(baseline) for _ in actors])
    if broken:
        outputs[1]["hook"] = outputs[0]["hook"]
    assert calls.qsize() == PARTIES, "copy hook must run once per independent call"
    expected = {"hook": {"copied": {"inner": [7, 8]}}, "sibling": [1, 2]}
    assert all(value == expected for value in outputs)
    _identities([value["hook"] for value in outputs])
    _identities([value["hook"]["copied"]["inner"] for value in outputs])
    outputs[0]["hook"]["copied"]["inner"].append(99)
    assert nested == {"inner": [7, 8]}
    assert outputs[1] == expected
    return {"actors": list(actors), "reentrant_copy_hooks": calls.qsize()}


def _rex_eviction(seed, *, broken=False):
    from unstd import rex

    rex.purge()
    eviction = _Gate("rex.duplicate_oldest_selected", PARTIES)
    selected = Queue()
    state = local()

    class InterleavedCache(dict):
        """Pause once per actor after choosing the oldest key, before deleting it."""

        def __delitem__(self, key):
            if not getattr(state, "entered", False):
                state.entered = True
                selected.put(key)
                eviction.wait()
            super().__delitem__(key)

    rex._cache = InterleavedCache()
    for index in range(rex._MAXCACHE):
        rex.compile(f"old-{index:03d}:[0-9]+")
    assert len(rex._cache) == rex._MAXCACHE
    gate = _Gate("rex.cold_compile_at_capacity", PARTIES)
    compile_native = rex._irgx_pattern
    admissions = Queue()

    def simultaneous(pattern, flags):
        if pattern.startswith("new-"):
            admissions.put(pattern)
            gate.wait()
        return compile_native(pattern, flags)

    rex._irgx_pattern = simultaneous
    actors = ordered(seed, "concurrency.rex.eviction", list(range(PARTIES)))

    def action(actor):
        pattern = f"new-{actor}:([0-9]+)"
        subject = f"x new-{actor}:{actor + 100} y"
        compiled = rex.compile(pattern)
        observed = compiled.search(subject)
        expected = re.search(pattern, subject)
        assert observed is not None
        assert expected is not None
        assert (observed.group(), observed.groups(), observed.span()) == (
            expected.group(),
            expected.groups(),
            expected.span(),
        )
        return compiled

    outputs = _parallel([lambda actor=actor: action(actor) for actor in actors])
    if broken:
        first = actors[0]
        rex._cache[(str, f"new-{first}:([0-9]+)", 0)] = re.compile("corrupted")
    assert admissions.qsize() == PARTIES
    oldest_keys = [selected.get_nowait() for _ in range(selected.qsize())]
    assert len(oldest_keys) == PARTIES
    assert set(oldest_keys) == {(str, "old-000:[0-9]+", 0)}, (
        "duplicate oldest-entry eviction interleaving did not occur"
    )
    assert len(rex._cache) <= rex._MAXCACHE, (
        f"concurrent cache admission retained {len(rex._cache)} entries; "
        f"documented capacity is {rex._MAXCACHE}"
    )
    # No purge runs during this observation: a retained hot entry is identical.
    for actor, compiled in zip(actors, outputs, strict=True):
        assert rex.compile(f"new-{actor}:([0-9]+)") is compiled
    return {
        "actors": list(actors),
        "entries": len(rex._cache),
        "duplicate_oldest_evictions": len(oldest_keys),
        "native_backend": rex._HAVE_IRGX,
    }


def _rex_retained_matches(seed, *, broken=False):
    from unstd import rex

    pattern = r"(?P<word>[A-Z]+):(?P<number>[0-9]+)"
    compiled = rex.compile(pattern)
    captured = _Gate("rex.matches_retained", PARTIES)
    purged = _Gate("rex.purge_completed", PARTIES)
    actors = ordered(seed, "concurrency.rex.matches", list(range(PARTIES)))

    def action(actor):
        subject = f"xx {'A' * (actor + 1)}:{actor + 100} yy"
        found = compiled.search(subject)
        expected = re.search(pattern, subject)
        assert found is not None
        assert expected is not None
        captured.wait()
        if actor == 0:
            rex.purge()
            rex.compile("unrelated-[0-9]+")
        purged.wait()
        if broken and actor == 1:
            found = compiled.search("xx CORRUPTED:999 yy")
        assert (found.group(), found.groups(), found.groupdict(), found.span()) == (
            expected.group(),
            expected.groups(),
            expected.groupdict(),
            expected.span(),
        )
        assert found.string == subject
        assert compiled.pattern == pattern
        return found

    outputs = _parallel([lambda actor=actor: action(actor) for actor in actors])
    _identities(outputs)
    return {"actors": list(actors), "retained_matches": len(outputs), "purges": 1}


def _rex_callbacks(seed, *, broken=False):
    from unstd import rex

    compiled = rex.compile(r"([0-9]+)")
    gate = _Gate("rex.replacement_callbacks", PARTIES)
    actors = ordered(seed, "concurrency.rex.callbacks", list(range(PARTIES)))

    def action(actor):
        subject = f"a{actor + 11}b{actor + 21}c"
        calls = []

        def replace(match):
            before = (match.group(), match.span())
            if not calls:
                gate.wait()
            inner = compiled.search("nested909")
            assert inner is not None
            assert inner.group() == "909"
            rex.purge()
            rex.compile("fresh-[a-z]+")
            assert (match.group(), match.span()) == before
            calls.append(before)
            return f"<{match.group()}>"

        result = compiled.subn(replace, subject)
        if broken:
            calls.append(calls[0])
        expected_calls = [
            (match.group(), match.span()) for match in re.finditer(r"([0-9]+)", subject)
        ]
        assert calls == expected_calls, "callback replay or match-state corruption"
        assert result == re.subn(r"([0-9]+)", r"<\1>", subject)
        return len(calls)

    outputs = _parallel([lambda actor=actor: action(actor) for actor in actors])
    return {"actors": list(actors), "callbacks": outputs}


def _rex_callback_failure(seed, *, broken=False):
    from unstd import rex

    compiled = rex.compile(r"([0-9]+)")
    gate = _Gate("rex.healthy_and_failing_callbacks", 2)
    # This is one of the native facade's typed transport-refusal exceptions.
    # Once a callback starts it must propagate, never trigger callback replay.
    sentinel = UnicodeEncodeError("utf-8", "\ud800", 0, 1, "callback sentinel")
    actors = ordered(seed, "concurrency.rex.callback_failure", ["raises", "healthy"])

    def action(actor):
        calls = []

        def replace(match):
            calls.append(match.group())
            if len(calls) == 1:
                gate.wait()
            if actor == "raises":
                raise sentinel
            return f"<{match.group()}>"

        if actor == "raises":
            with pytest.raises(UnicodeEncodeError) as raised:
                compiled.subn(replace, "a11b22")
            assert raised.value is sentinel
            if broken:
                calls.append("11")
            assert calls == ["11"], "failing callback was replayed"
        else:
            assert compiled.subn(replace, "a33b44") == ("a<33>b<44>", 2)
            assert calls == ["33", "44"]
        return {"actor": actor, "calls": calls}

    return {
        "actors": _parallel([lambda actor=actor: action(actor) for actor in actors])
    }


def _codec_shared(seed, *, broken=False):
    import msgspec

    from unstd.serde.structs import Codec

    class Record(msgspec.Struct):
        actor: int
        payload: list[dict[str, str]]

    codec = Codec(Record)
    actors = ordered(seed, "concurrency.codec.shared", list(range(PARTIES)))
    encoded = _Gate("codec.encoded", PARTIES)
    decoded = _Gate("codec.decoded", PARTIES)
    retained = Queue()

    def action(actor):
        source = Record(actor, [{"value": f"actor-{actor}-" + "x" * 4096}])
        expected_raw = json.dumps(
            {"actor": source.actor, "payload": source.payload}, separators=(",", ":")
        ).encode()
        raw = codec.encode(source)
        encoded.wait()
        result = codec.decode(raw)
        retained.put(result)
        decoded.wait()
        if broken and actor == 0:
            result.actor = 99
        assert raw == expected_raw
        assert result == source
        assert result is not source
        assert result.payload is not source.payload
        result.payload[0]["value"] = f"mutated-{actor}"
        assert source.payload[0]["value"].startswith(f"actor-{actor}-")
        assert codec.decode_or(b'{"actor":"bad","payload":[]}', source) is source
        recovered = codec.decode(expected_raw)
        assert recovered == source
        assert recovered.payload is not result.payload
        return actor

    outputs = _parallel([lambda actor=actor: action(actor) for actor in actors])
    decoded_rows = [retained.get_nowait() for _ in outputs]
    _identities(decoded_rows)
    _identities([result.payload for result in decoded_rows])
    return {"actors": outputs, "decodes": len(decoded_rows), "payload_bytes": 4096}


def _codec_policies(seed, *, broken=False):
    import msgspec

    from unstd.serde.structs import Codec

    class Record(msgspec.Struct):
        value: int

    strict, lax = Codec(Record), Codec(Record, strict=False)
    if broken:
        strict = lax
    raw = b'{"value":"123"}'
    actors = ordered(
        seed, "concurrency.codec.policies", ["strict", "lax", "strict", "lax"]
    )
    gate = _Gate("codec.strict_and_lax", PARTIES)

    def action(policy):
        selected = strict if policy == "strict" else lax
        gate.wait()
        if policy == "strict":
            caught = None
            try:
                selected.decode(raw)
            except msgspec.ValidationError as error:
                caught = error
            assert caught is not None, "strict codec adopted another codec's policy"
            assert selected.decode_or(raw) is None
        else:
            assert selected.decode(raw) == Record(123)
            assert selected.decode_or(raw) == Record(123)
        assert selected.decode(b'{"value":7}') == Record(7)
        return policy

    return {
        "policies": _parallel([lambda actor=actor: action(actor) for actor in actors])
    }


def _yaml_iterators(seed, *, broken=False):
    from unstd import yaml

    actors = ordered(seed, "concurrency.yaml.iterators", list(range(PARTIES)))
    loaded = _Gate("yaml.first_document_loaded", PARTIES)
    iterations = _Gate("yaml.second_document_loaded", PARTIES)

    def action(actor):
        source = io.StringIO(
            f"base: &same [{actor}, {actor + 1}]\ncopy: *same\n---\n"
            f"base: &same [{actor + 10}]\ncopy: *same\n"
        )
        documents = yaml.load_all(source)
        try:
            first = next(documents)
            loaded.wait()
            if broken:
                first["copy"] = list(first["base"])
            assert first == {"base": [actor, actor + 1], "copy": [actor, actor + 1]}
            assert first["base"] is first["copy"], (
                "alias identity lost inside a document"
            )
            second = next(documents)
            iterations.wait()
            assert second == {"base": [actor + 10], "copy": [actor + 10]}
            assert second["base"] is second["copy"]
            assert second["base"] is not first["base"]
            assert list(documents) == []
            first["base"].append(999)
            assert second["base"] == [actor + 10]
        finally:
            documents.close()
        assert not source.closed, "caller-owned stream was closed"
        source.close()
        return actor

    return {
        "actors": _parallel([lambda actor=actor: action(actor) for actor in actors]),
        "documents": PARTIES * 2,
    }


def _yaml_reentry(seed, *, broken=False):
    from unstd import yaml

    actors = ordered(seed, "concurrency.yaml.reentry", list(range(PARTIES)))
    gate = _Gate("yaml.outer_read_reentry", PARTIES)

    def action(actor):
        entered = []

        class ReentrantReader(io.StringIO):
            def read(self, size=-1):
                if not entered:
                    entered.append(True)
                    gate.wait()
                    nested = yaml.loads(f"inner: &same [{actor + 50}]\ncopy: *same\n")
                    assert nested == {"inner": [actor + 50], "copy": [actor + 50]}
                    assert nested["inner"] is nested["copy"]
                return super().read(size)

        source = ReentrantReader(f"outer: &same [{actor}]\ncopy: *same\n")
        try:
            outer = yaml.load(source)
            if broken:
                outer["outer"].append(999)
            assert entered == [True]
            assert outer == {"outer": [actor], "copy": [actor]}
            assert outer["outer"] is outer["copy"]
            assert not source.closed
        finally:
            source.close()
        return actor

    return {
        "actors": _parallel([lambda actor=actor: action(actor) for actor in actors]),
        "nested_parsers": PARTIES,
    }


def _yaml_failure_isolation(seed, *, broken=False):
    from unstd import yaml

    gate = _Gate("yaml.invalid_and_healthy", PARTIES)
    actors = ordered(
        seed, "concurrency.yaml.failure", ["duplicate", "unknown", "editable", "safe"]
    )
    sessions = Queue()
    engine = yaml._engine

    def own_engine(**kwargs):
        parser = engine(**kwargs)
        sessions.put(parser)
        return parser

    yaml._engine = own_engine

    def action(actor):
        gate.wait()
        if actor in {"duplicate", "unknown"}:
            text = (
                "key: 1\nkey: 2\n" if actor == "duplicate" else "key: !unknown value\n"
            )
            with pytest.raises(yaml.YAMLError):
                yaml.loads(text)
        elif actor == "editable":
            result = yaml.parse('# retained\nlabel: "yes"\n')
            text = yaml.dumps(result)
            assert "# retained" in text
            assert '"yes"' in text
        else:
            assert yaml.loads("yes: yes\nvalue: true\n") == {
                "yes": "yes",
                "value": True,
            }
        assert yaml.loads("recovered: &same [7]\ncopy: *same\n") == {
            "recovered": [7],
            "copy": [7],
        }
        return actor

    results = _parallel([lambda actor=actor: action(actor) for actor in actors])
    instances = [sessions.get_nowait() for _ in range(sessions.qsize())]
    if broken:
        instances[-1] = instances[0]
    assert len(instances) == 9
    _identities(instances)
    return {"actors": results, "private_parser_sessions": len(instances)}


def _map_forks(seed, *, broken=False):
    from unstd.containers import Map

    @dataclasses.dataclass(frozen=True)
    class Key:
        value: int

        def __hash__(self):
            return 17

    baseline_dict = {Key(index): (index, f"value-{index}") for index in range(32)}
    baseline = Map(baseline_dict)
    snapshots = _Gate("map.branch_snapshots_retained", PARTIES)
    actors = ordered(seed, "concurrency.map.forks", list(range(PARTIES)))

    def action(actor):
        branch = baseline
        expected = dict(baseline_dict)
        retained = []
        for step in range(8):
            key = Key(actor * 8 + step)
            previous = branch
            branch = branch.set(key, (actor, f"branch-{step}"))
            expected[key] = (actor, f"branch-{step}")
            retained.append((previous, dict(previous)))
        snapshots.wait()
        removed = Key((actor + 1) * 8 % 32)
        branch = branch.delete(removed)
        del expected[removed]
        if broken:
            branch = baseline
        assert dict(branch) == expected
        assert branch is not baseline
        assert dict(baseline) == baseline_dict
        for snapshot, reference in retained:
            assert dict(snapshot) == reference
        assert len(baseline) == 32
        assert len(branch) == 31
        return {"actor": actor, "snapshots": len(retained), "branch_size": len(branch)}

    return {
        "actors": _parallel([lambda actor=actor: action(actor) for actor in actors]),
        "colliding_keys": 32,
    }


SCENARIOS = {
    "clone_admission": _clone_admission,
    "clone_fields": _clone_fields,
    "clone_reentry": _clone_reentry,
    "rex_eviction": _rex_eviction,
    "rex_retained_matches": _rex_retained_matches,
    "rex_callbacks": _rex_callbacks,
    "rex_callback_failure": _rex_callback_failure,
    "codec_shared": _codec_shared,
    "codec_policies": _codec_policies,
    "yaml_iterators": _yaml_iterators,
    "yaml_reentry": _yaml_reentry,
    "yaml_failure_isolation": _yaml_failure_isolation,
    "map_forks": _map_forks,
}


def main():
    """Execute one fixed scenario and return only bounded JSON evidence."""
    scenario, seed_text, mode = sys.argv[1:]
    assert scenario in SCENARIOS
    assert mode in {"normal", "broken"}
    observations = SCENARIOS[scenario](int(seed_text), broken=mode == "broken")
    events = [_EVENTS.get_nowait() for _ in range(_EVENTS.qsize())]
    assert len(events) <= 100
    sys.stdout.write(
        json.dumps(
            {"scenario": scenario, "observations": observations, "events": events}
        )
    )
