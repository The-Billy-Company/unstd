"""Path normalization preserves protocol ordering and ignores subclass hooks.

Filesystem helpers accept path values, not a custom Path subclass's I/O policy.
Optimizing ordinary Path inputs must retain normalization for other path objects
and must not move coercion failures past the first filesystem mutation.
Reusing path values must also observe later directory and file-state changes.
"""

from __future__ import annotations

from functools import partial
import inspect
import json
import os
from pathlib import Path
import stat
import sys
from types import MemberDescriptorType
from typing import Literal, Self, override

import pytest

from tests.support.environment.process import run_python
from unstd import fs


class _Argument:
    def __init__(
        self,
        value: str,
        name: str,
        events: list[str],
        failure: Exception | None = None,
    ) -> None:
        self.value = value
        self.name = name
        self.events = events
        self.failure = failure

    def __fspath__(self) -> str:
        self.events.append(self.name)
        if self.failure is not None:
            raise self.failure
        return self.value


_DIRECTORY_AUDIT = """
import json
import os
from pathlib import Path
import sys
from unstd import fs
target = Path(sys.argv[1]) / "directory"
if sys.argv[2] == "existing":
    target.mkdir()
    if len(sys.argv) == 4:
        fs.ensure_dir(target, check_first=False)
events: list[str] = []
def audit(event: str, arguments: tuple[object, ...]) -> None:
    if event == "os.mkdir":
        path = arguments[0]
        if isinstance(path, (str, os.PathLike)) and os.fspath(path) == str(target):
            events.append(event)
            raise RuntimeError("directory creation denied by audit policy")
sys.addaudithook(audit)
try:
    if len(sys.argv) == 4:
        assert sys.argv[3] == "mkdir"
        result = fs.ensure_dir(target, check_first=False)
    else:
        result = fs.ensure_dir(target)
except RuntimeError as error:
    assert str(error) == "directory creation denied by audit policy"
    outcome = "denied"
else:
    assert result == target
    outcome = "ok"
print(json.dumps({"outcome": outcome, "events": events, "exists": target.is_dir()}))
"""


@pytest.mark.parametrize("durable", [False, True, "strict"])
def test_atomic_replace_coerces_destination_then_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, durable: bool | Literal["strict"]
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"new")
    dest = tmp_path / "destination"
    dest.write_bytes(b"old")
    events: list[str] = []
    replacement = Path.replace

    def replace(path: Path, target: Path) -> Path:
        events.append("replace")
        return replacement(path, target)

    def sync(path: Path, *, strict: bool = False) -> None:
        assert path == tmp_path
        assert strict is (durable == "strict")
        events.append("sync")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(fs, "sync_dir", sync)
    result = fs.atomic_replace(
        _Argument(str(source), "source", events),
        _Argument(str(dest), "destination", events),
        durable=durable,
    )

    assert result == dest
    assert type(result) is type(tmp_path)
    assert events == ["destination", "source"] + (
        ["sync", "replace", "sync"]
        if durable == "strict"
        else ["replace", "sync"]
        if durable
        else ["replace"]
    )
    assert dest.read_bytes() == b"new"
    assert not source.exists()


@pytest.mark.parametrize("failing", ["destination", "source"])
def test_atomic_replace_coercion_failure_preserves_both_files(
    tmp_path: Path, failing: str
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"new")
    dest = tmp_path / "destination"
    dest.write_bytes(b"old")
    events: list[str] = []
    failure = ValueError("refused path")

    with pytest.raises(ValueError, match="refused path") as caught:
        fs.atomic_replace(
            _Argument(
                str(source),
                "source",
                events,
                failure if failing == "source" else None,
            ),
            _Argument(
                str(dest),
                "destination",
                events,
                failure if failing == "destination" else None,
            ),
            durable="strict",
        )

    assert caught.value is failure
    assert events == (
        ["destination"] if failing == "destination" else ["destination", "source"]
    )
    assert source.read_bytes() == b"new"
    assert dest.read_bytes() == b"old"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["destination", "source"]


@pytest.mark.parametrize("operation", ["directory", "replace"])
def test_helpers_do_not_add_path_protocol_interactions(
    tmp_path: Path, operation: str
) -> None:
    events: list[str] = []

    def claimed_class(value: _Argument) -> type[object]:
        value.events.append(f"{value.name}.class")
        return object

    argument_type = type(
        "Argument", (_Argument,), {"__class__": property(claimed_class)}
    )

    destination = tmp_path / "destination"
    target: object = argument_type(str(destination), "destination", events)
    assert isinstance(target, _Argument)
    source = tmp_path / "source"
    source.write_bytes(b"new")
    origin: object = argument_type(str(source), "source", events)
    assert isinstance(origin, _Argument)
    Path(target)
    if operation == "replace":
        Path(origin)
    expected = list(events)
    events.clear()

    if operation == "replace":
        assert fs.atomic_replace(origin, target) == destination
        assert destination.read_bytes() == b"new"
        assert not source.exists()
    else:
        assert fs.ensure_dir(target) == destination
        assert destination.is_dir()
    assert events == expected


def test_helpers_normalize_path_subclasses_before_io(tmp_path: Path) -> None:
    class PolicyPath(Path):
        @override
        def __str__(self) -> str:
            pytest.fail("Path subclass string hook was called")

        @override
        def __fspath__(self) -> str:
            pytest.fail("Path subclass filesystem hook was called")

        @override
        def is_dir(self) -> bool:
            pytest.fail("Path subclass directory hook was called")

        @override
        def mkdir(self, *args: object, **kwargs: object) -> None:
            pytest.fail("Path subclass mkdir hook was called")

        @override
        def replace(self, target: str | os.PathLike[str]) -> Self:
            pytest.fail("Path subclass replacement hook was called")

        @property
        @override
        def parent(self) -> Self:
            pytest.fail("Path subclass parent hook was called")

    directory = tmp_path / "directory"
    created = fs.ensure_dir(PolicyPath(directory))
    assert created == directory
    assert type(created) is type(tmp_path)
    assert directory.is_dir()
    assert fs.ensure_dir(PolicyPath(directory)) == directory

    source = directory / "source"
    source.write_bytes(b"replace")
    dest = directory / "destination"
    replaced = fs.atomic_replace(PolicyPath(source), PolicyPath(dest))
    assert replaced == dest
    assert type(replaced) is type(tmp_path)
    assert dest.read_bytes() == b"replace"
    assert not source.exists()

    written = fs.atomic_write(PolicyPath(dest), b"write")
    assert written == dest
    assert type(written) is type(tmp_path)
    with fs.atomic_writer(PolicyPath(dest)) as stream:
        stream.write("writer")
    assert dest.read_text() == "writer"


def test_empty_path_preserves_pathlib_current_directory_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert fs.ensure_dir("") == Path()
    dest = tmp_path / "destination"
    dest.write_bytes(b"retained")

    with pytest.raises(OSError) as expected:  # noqa: PT011 -- Compare exact OS details below.
        Path("").replace(dest)  # noqa: PTH201 -- The empty argument is the normalization boundary.
    with pytest.raises(type(expected.value)) as observed:
        fs.atomic_replace("", dest)

    assert observed.value.errno == expected.value.errno
    assert observed.value.filename == expected.value.filename
    assert observed.value.filename2 == expected.value.filename2
    assert dest.read_bytes() == b"retained"


@pytest.mark.parametrize("replacement", ["missing", "file"])
@pytest.mark.parametrize("check_first", [None, False], ids=["default", "direct"])
def test_ensure_dir_rechecks_a_previously_created_path(
    tmp_path: Path, replacement: str, check_first: bool | None
) -> None:
    ensure = (
        fs.ensure_dir
        if check_first is None
        else partial(fs.ensure_dir, check_first=check_first)
    )
    directory = tmp_path / "directory"
    assert ensure(directory) == directory
    # A repeated successful call must not hide a later external replacement.
    assert ensure(directory) == directory
    directory.rmdir()

    if replacement == "file":
        directory.write_bytes(b"retained replacement")
        with pytest.raises(FileExistsError):
            ensure(directory)
        assert directory.read_bytes() == b"retained replacement"
    else:
        assert ensure(directory) == directory
        assert directory.is_dir()


def test_repeated_replace_publishes_each_new_source_before_returning(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    destination.write_bytes(b"original")

    for payload in (b"first complete version", b"next version", b"next version"):
        previous = destination.stat()
        source.write_bytes(payload)
        proposed = source.stat()
        assert (proposed.st_dev, proposed.st_ino) != (previous.st_dev, previous.st_ino)

        assert fs.atomic_replace(source, destination) == destination

        committed = destination.stat()
        assert not source.exists()
        assert destination.read_bytes() == payload
        assert (committed.st_dev, committed.st_ino) == (
            proposed.st_dev,
            proposed.st_ino,
        )


@pytest.mark.parametrize("state", ["existing", "missing"])
def test_directory_creation_audit_policy_is_observed_only_when_creation_is_needed(
    tmp_path: Path, state: str
) -> None:
    result = run_python(
        _DIRECTORY_AUDIT, [str(tmp_path), state], cwd=tmp_path, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == (
        {"outcome": "ok", "events": [], "exists": True}
        if state == "existing"
        else {"outcome": "denied", "events": ["os.mkdir"], "exists": False}
    )


@pytest.mark.parametrize("state", ["existing", "missing"])
def test_direct_directory_policy_retains_creation_audit_denial(
    tmp_path: Path, state: str
) -> None:
    result = run_python(
        _DIRECTORY_AUDIT, [str(tmp_path), state, "mkdir"], cwd=tmp_path, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "outcome": "denied",
        "events": ["os.mkdir"],
        "exists": state == "existing",
    }


@pytest.mark.parametrize("kind", ["text", "path", "protocol"])
def test_direct_directory_policy_normalizes_path_once(
    tmp_path: Path, kind: str
) -> None:
    target = tmp_path / "directory"
    events: list[str] = []
    value: str | Path | _Argument = (
        str(target)
        if kind == "text"
        else target
        if kind == "path"
        else _Argument(str(target), "directory", events)
    )
    expected = Path(value)
    interactions = list(events)
    events.clear()

    observed = fs.ensure_dir(value, check_first=False)

    assert observed == expected
    assert type(observed) is type(tmp_path)
    assert observed.is_dir()
    assert events == interactions


def test_direct_directory_policy_normalizes_subclass_io_policy(tmp_path: Path) -> None:
    class PolicyPath(Path):
        @override
        def __fspath__(self) -> str:
            pytest.fail("Path subclass filesystem hook was called")

        @override
        def mkdir(self, *args: object, **kwargs: object) -> None:
            pytest.fail("Path subclass mkdir hook was called")

    target = tmp_path / "directory"
    observed = fs.ensure_dir(PolicyPath(target), check_first=False)
    assert observed == target
    assert type(observed) is type(tmp_path)
    assert target.is_dir()


def test_direct_directory_policy_coercion_failure_precedes_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = ValueError("refused path")
    events: list[str] = []
    value = _Argument(str(tmp_path / "directory"), "directory", events, failure)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Path.mkdir was called before path coercion completed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", forbidden)
        with pytest.raises(ValueError, match="refused path") as caught:
            fs.ensure_dir(value, check_first=False)
    assert caught.value is failure
    assert events == ["directory"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "state",
    [
        "existing",
        "missing",
        "file",
        "parent-file",
        "dir-link",
        "file-link",
        "broken-link",
    ],
)
def test_direct_directory_policy_matches_path_mkdir_state_and_errors(
    tmp_path: Path, state: str
) -> None:
    if os.name == "nt" and state.endswith("-link"):
        pytest.skip("POSIX symlink fixtures")

    def prepare(root: Path) -> Path:
        root.mkdir()
        target = root / "directory"
        if state == "existing":
            target.mkdir()
        elif state == "missing":
            target = root / "parent" / "directory"
        elif state == "file":
            target.write_bytes(b"retained")
        elif state == "parent-file":
            (root / "parent").write_bytes(b"retained parent")
            target = root / "parent" / "directory"
        else:
            referent = root / "referent"
            if state == "dir-link":
                referent.mkdir()
            elif state == "file-link":
                referent.write_bytes(b"retained referent")
            target.symlink_to("referent", target_is_directory=state == "dir-link")
        return target

    def snapshot(root: Path) -> list[tuple[str, int, int, str | bytes | None]]:
        return [
            (
                str(path.relative_to(root)),
                stat.S_IFMT(path.lstat().st_mode),
                stat.S_IMODE(path.lstat().st_mode),
                str(path.readlink())
                if path.is_symlink()
                else path.read_bytes()
                if path.is_file()
                else None,
            )
            for path in sorted(root.rglob("*"))
        ]

    reference_root = tmp_path / "reference"
    observed_root = tmp_path / "observed"
    reference = prepare(reference_root)
    observed = prepare(observed_root)
    reference_error: OSError | None = None
    observed_error: OSError | None = None
    result: Path | None = None
    try:
        reference.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        reference_error = error
    try:
        result = fs.ensure_dir(observed, check_first=False)
    except OSError as error:
        observed_error = error
    if reference_error is None:
        assert observed_error is None
        assert result == observed
        assert type(result) is type(tmp_path)
    else:
        assert observed_error is not None
        assert type(observed_error) is type(reference_error)
        assert observed_error.errno == reference_error.errno
        assert isinstance(reference_error.filename, str)
        assert isinstance(observed_error.filename, str)
        assert Path(observed_error.filename).relative_to(observed_root) == Path(
            reference_error.filename
        ).relative_to(reference_root)
        assert observed_error.filename2 == reference_error.filename2
    assert snapshot(observed_root) == snapshot(reference_root)


@pytest.mark.parametrize("check_first", [None, False], ids=["default", "direct"])
def test_returned_directory_path_has_fresh_metadata_without_changing_input_snapshot(
    tmp_path: Path, check_first: bool | None
) -> None:
    if sys.version_info < (3, 14):
        pytest.skip("Path.info is available on Python 3.14 and later")
    directory = tmp_path / "directory"
    assert not directory.info.exists()
    ensure = (
        fs.ensure_dir
        if check_first is None
        else partial(fs.ensure_dir, check_first=check_first)
    )

    result = ensure(directory)

    assert result.is_dir()
    assert not directory.info.exists()
    assert result.info.is_dir()


def test_returned_replacement_path_has_fresh_metadata_without_changing_input_snapshot(
    tmp_path: Path,
) -> None:
    if sys.version_info < (3, 14):
        pytest.skip("Path.info is available on Python 3.14 and later")
    source = tmp_path / "source"
    source.write_bytes(b"new complete contents")
    destination = tmp_path / "destination"
    assert not destination.info.exists()

    result = fs.atomic_replace(source, destination)

    assert result.is_file()
    assert result.read_bytes() == b"new complete contents"
    assert not destination.info.exists()
    assert result.info.is_file()


@pytest.mark.parametrize("check_first", [None, False], ids=["default", "direct"])
def test_returned_directory_metadata_is_independent_of_later_input_queries(
    tmp_path: Path, check_first: bool | None
) -> None:
    if sys.version_info < (3, 14):
        pytest.skip("Path.info is available on Python 3.14 and later")
    directory = tmp_path / "directory"
    ensure = (
        fs.ensure_dir
        if check_first is None
        else partial(fs.ensure_dir, check_first=check_first)
    )
    result = ensure(directory)
    assert directory.info.exists()

    directory.rmdir()

    assert not result.is_dir()
    assert directory.info.exists()
    assert not result.info.exists()


def test_returned_replacement_metadata_is_independent_of_later_input_queries(
    tmp_path: Path,
) -> None:
    if sys.version_info < (3, 14):
        pytest.skip("Path.info is available on Python 3.14 and later")
    source = tmp_path / "source"
    source.write_bytes(b"new complete contents")
    destination = tmp_path / "destination"
    result = fs.atomic_replace(source, destination)
    assert destination.info.exists()

    destination.unlink()

    assert not result.is_file()
    assert destination.info.exists()
    assert not result.info.exists()


@pytest.mark.parametrize("owner_name", ["base", "concrete"])
@pytest.mark.parametrize("restore_inside", [False, True])
def test_constructor_redirect_is_not_overwritten_by_cached_input_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_name: str,
    restore_inside: bool,
) -> None:
    requested = tmp_path / "requested"
    redirected = tmp_path / "redirected"
    requested_text = str(requested)
    redirected_text = str(redirected)
    owner = Path if owner_name == "base" else type(requested)
    initialize = owner.__init__
    reached: list[Path] = []

    def redirect(self: Path, *args: str | os.PathLike[str]) -> None:
        if len(args) == 1 and args[0] is requested:
            reached.append(self)
            initialize(self, redirected_text)
            if restore_inside:
                patch.setattr(owner, "__init__", initialize)
        else:
            initialize(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(owner, "__init__", redirect)
        result = fs.ensure_dir(requested)
        assert str(result) == redirected_text
    assert len(reached) == 1
    assert redirected.is_dir()
    assert not Path(requested_text).exists()


@pytest.mark.parametrize("restore_inside", [False, True])
def test_factory_redirect_is_not_overwritten_by_cached_input_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_inside: bool
) -> None:
    requested = tmp_path / "requested"
    redirected = tmp_path / "redirected"
    requested_text = str(requested)
    redirected_text = str(redirected)
    factory = Path
    assert vars(fs)["Path"] is factory
    reached: list[tuple[str | os.PathLike[str], ...]] = []

    def redirect(*args: str | os.PathLike[str]) -> Path:
        reached.append(args)
        result = factory(redirected_text) if args == (requested,) else factory(*args)
        if restore_inside:
            patch.setattr(fs, "Path", factory)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(fs, "Path", redirect)
        result = fs.ensure_dir(requested)
        assert str(result) == redirected_text
    assert reached == [(requested,)]
    assert redirected.is_dir()
    assert not Path(requested_text).exists()


def test_new_constructor_failure_stays_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = tmp_path / "requested"
    str(requested)
    allocate = Path.__new__
    failure = RuntimeError("constructor refused path")

    def refuse(cls: type[Path], *args: str | os.PathLike[str]) -> Path:
        if len(args) == 1 and args[0] is requested:
            raise failure
        return allocate(cls, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "__new__", staticmethod(refuse))
        with pytest.raises(RuntimeError, match="constructor refused path") as caught:
            fs.ensure_dir(requested)
    assert caught.value is failure
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("owner_name", ["base", "concrete"])
@pytest.mark.parametrize(
    ("binding", "restore_inside"),
    [
        ("__str__", False),
        ("__fspath__", False),
        ("__getattribute__", False),
        ("__getattribute__", True),
        ("__setattr__", False),
        ("__setattr__", True),
    ],
)
def test_structural_path_redirect_is_not_overwritten_by_cached_input_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_name: str,
    binding: str,
    restore_inside: bool,
) -> None:
    requested = tmp_path / "requested"
    redirected = tmp_path / "redirected"
    requested_text = str(requested)
    redirected_text = str(redirected)
    owner = Path if owner_name == "base" else type(requested)
    getattribute = owner.__getattribute__
    setattr_original = owner.__setattr__

    def text(self: Path) -> str:
        try:
            cached: object = getattribute(self, "_str")
        except AttributeError:
            return redirected_text
        assert isinstance(cached, str)
        return cached

    def get(self: Path, name: str) -> object:
        if self is requested and name == "_raw_paths":
            if restore_inside:
                patch.setattr(owner, "__getattribute__", getattribute)
            return [redirected_text]
        return getattribute(self, name)

    def put(self: Path, name: str, value: object) -> None:
        if self is not requested and name == "_raw_paths":
            value = [redirected_text]
        setattr_original(self, name, value)
        if name == "_raw_paths" and restore_inside:
            patch.setattr(owner, "__setattr__", setattr_original)

    with monkeypatch.context() as patch:
        if binding == "__getattribute__":
            patch.setattr(owner, binding, get)
        elif binding == "__setattr__":
            patch.setattr(owner, binding, put)
        else:
            patch.setattr(owner, binding, text)
        result = fs.ensure_dir(requested)
        assert result.is_dir()
    assert redirected.is_dir()
    assert not Path(requested_text).exists()


@pytest.mark.parametrize("owner_name", ["base", "concrete"])
def test_lexical_descriptor_redirect_is_not_overwritten_by_cached_input_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner_name: str
) -> None:
    requested = tmp_path / "requested"
    redirected = tmp_path / "redirected"
    requested_text = str(requested)
    redirected_text = str(redirected)
    slot = inspect.getattr_static(type(requested), "_str", None)
    if not isinstance(slot, MemberDescriptorType):
        pytest.skip("CPython lexical-cache descriptor control")
    owner = Path if owner_name == "base" else type(requested)

    def text(self: Path) -> str:
        try:
            cached: object = slot.__get__(self, type(self))
        except AttributeError:
            return redirected_text
        assert isinstance(cached, str)
        return cached

    with monkeypatch.context() as patch:
        patch.setattr(owner, "_str", property(text))
        result = fs.ensure_dir(requested)
        assert result.is_dir()
    assert redirected.is_dir()
    assert not Path(requested_text).exists()


def test_replacement_constructor_order_stays_destination_then_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"new")
    str(destination)
    initialize = Path.__init__
    events: list[str] = []

    def record(self: Path, *args: str | os.PathLike[str]) -> None:
        if len(args) == 1:
            if args[0] is destination:
                events.append("destination")
            elif args[0] is source:
                events.append("source")
        initialize(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "__init__", record)
        assert fs.atomic_replace(source, destination) == destination
    assert events == ["destination", "source"]
    assert destination.read_bytes() == b"new"
    assert not source.exists()


def test_replacement_source_constructor_failure_precedes_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    initialize = Path.__init__
    failure = RuntimeError("source constructor refused path")
    events: list[str] = []

    def refuse(self: Path, *args: str | os.PathLike[str]) -> None:
        if len(args) == 1:
            if args[0] is destination:
                events.append("destination")
            elif args[0] is source:
                events.append("source")
                raise failure
        initialize(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "__init__", refuse)
        with pytest.raises(
            RuntimeError, match="source constructor refused path"
        ) as caught:
            fs.atomic_replace(source, destination)
    assert caught.value is failure
    assert events == ["destination", "source"]
    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"


@pytest.mark.parametrize("redirected_argument", ["source", "destination"])
@pytest.mark.parametrize("restore_inside", [False, True])
def test_replacement_constructor_redirect_is_not_overwritten_by_cached_input_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    redirected_argument: str,
    restore_inside: bool,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"ordinary source")
    destination.write_bytes(b"old destination")
    redirected = tmp_path / "redirected"
    redirected_text = str(redirected)
    if redirected_argument == "source":
        redirected.write_bytes(b"redirected source")
    argument = source if redirected_argument == "source" else destination
    str(argument)
    initialize = Path.__init__
    events: list[str] = []

    def redirect(self: Path, *args: str | os.PathLike[str]) -> None:
        if len(args) == 1 and args[0] is argument:
            events.append(redirected_argument)
            initialize(self, redirected_text)
            if restore_inside:
                patch.setattr(Path, "__init__", initialize)
        else:
            initialize(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "__init__", redirect)
        result = fs.atomic_replace(source, destination)
    assert events == [redirected_argument]
    if redirected_argument == "source":
        assert result == destination
        assert source.read_bytes() == b"ordinary source"
        assert destination.read_bytes() == b"redirected source"
        assert not redirected.exists()
    else:
        assert result == redirected
        assert not source.exists()
        assert destination.read_bytes() == b"old destination"
        assert redirected.read_bytes() == b"ordinary source"


@pytest.mark.parametrize("check_first", [None, False])
def test_directory_normalization_reobserves_string_subclass_components(
    tmp_path: Path, check_first: bool | None
) -> None:
    initial = tmp_path / "initial"
    redirected = tmp_path / "redirected"
    initial.mkdir()
    redirected.mkdir()
    redirected_text = str(redirected)

    class Dynamic(str):
        changed = False

        @override
        def __add__(self, suffix: str) -> str:
            return (redirected_text if self.changed else str(self)) + suffix

    component = Dynamic(str(initial))
    requested = Path(component, "leaf")
    assert str(requested) == str(initial / "leaf")
    component.changed = True
    expected = Path(requested)
    if os.name == "posix":
        assert str(expected) == str(redirected / "leaf")

    if check_first is None:
        result = fs.ensure_dir(requested)
    else:
        result = fs.ensure_dir(requested, check_first=check_first)

    assert str(result) == str(expected)
    assert expected.is_dir()
    unused = redirected if expected.parent == initial else initial
    assert not (unused / "leaf").exists()
