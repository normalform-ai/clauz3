"""Imported policy helpers resolve from bounded source files without execution."""

import sys
from pathlib import Path

import astroid
import pytest
from astroid import nodes
from astroid.manager import AstroidManager

from clauz3 import policy_imports
from clauz3.policy import DomainPolicy, compute_coverage, transitive_calls
from clauz3.policy_calls import DYNAMIC_CALL

_POLICIES = [
    DomainPolicy(when_used=("record",), required=("silent",)),
    DomainPolicy(when_used=("audit",), required=("logged",)),
]


def _write(package: Path, name: str, source: str) -> Path:
    path = package / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


@pytest.fixture
def package(tmp_path: Path) -> Path:
    package = tmp_path / "sample_policy"
    _write(package, "__init__.py", "")
    _write(package, "trusted/__init__.py", "")
    _write(
        package,
        "trusted/effects.py",
        "import clauz3\n@clauz3.trusted\ndef record(value: str) -> None:\n    pass\n",
    )
    return package


def _entry(module: str, name: str = "run") -> str:
    return (
        f"from sample_policy.{module} import {name}\n"
        f"def main() -> None:\n    {name}()\n"
    )


def _calls(source: str, package: Path) -> set[str]:
    return transitive_calls(
        source,
        "main",
        import_roots=[package.parent],
        trusted_roots=[package / "trusted"],
    )


def _coverage(source: str, package: Path) -> list[dict[str, object]]:
    return compute_coverage(
        source=source,
        guarantees=[],
        policies=_POLICIES,
        import_roots=[package.parent],
        trusted_roots=[package / "trusted"],
    )


@pytest.mark.parametrize("under_trusted_root", [False, True])
def test_plain_imported_helper_chain_reaches_effect(
    package: Path, under_trusted_root: bool
) -> None:
    directory = "trusted/" if under_trusted_root else ""
    relative_effects = ".effects" if under_trusted_root else ".trusted.effects"
    _write(
        package,
        f"{directory}helpers.py",
        "from .middle import step\ndef run() -> None:\n    step()\n",
    )
    _write(
        package,
        f"{directory}middle.py",
        f"from {relative_effects} import record\n"
        'def step() -> None:\n    record("one")\n',
    )
    source = _entry("trusted.helpers" if under_trusted_root else "helpers")

    calls = _calls(source, package)

    assert {"run", "step", "record"} <= calls
    assert DYNAMIC_CALL not in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == ["record"]


def test_package_reexports_and_parent_relative_imports_reach_effect(
    package: Path,
) -> None:
    _write(package, "__init__.py", "from .api import launch as run\n")
    _write(package, "api/__init__.py", "from .worker import launch\n")
    _write(
        package,
        "api/worker.py",
        "from ..trusted.effects import record as emit\n"
        'def launch() -> None:\n    emit("one")\n',
    )
    source = "from sample_policy import run\ndef main() -> None:\n    run()\n"

    calls = _calls(source, package)

    assert {"launch", "record"} <= calls
    assert DYNAMIC_CALL not in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == ["record"]


def test_imported_callable_assignment_alias_reaches_effect(package: Path) -> None:
    _write(
        package,
        "helpers.py",
        "from .trusted.effects import record as send\n"
        "deliver = send\n"
        'def run() -> None:\n    deliver("one")\n',
    )

    calls = _calls(_entry("helpers"), package)

    assert "record" in calls
    assert DYNAMIC_CALL not in calls


def test_mutually_recursive_imported_helpers_terminate_and_keep_effect(
    package: Path,
) -> None:
    _write(
        package,
        "first.py",
        "from .second import second\ndef first() -> None:\n    second()\n",
    )
    _write(
        package,
        "second.py",
        "from .first import first\n"
        "from .trusted.effects import record\n"
        'def second() -> None:\n    first()\n    record("one")\n',
    )

    calls = _calls(_entry("first", "first"), package)

    assert {"first", "second", "record"} <= calls
    assert DYNAMIC_CALL not in calls


def test_source_resolution_does_not_execute_package_initializers(package: Path) -> None:
    marker = package.parent / "initializer-ran"
    _write(
        package,
        "__init__.py",
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('package initializer must not run')\n",
    )
    _write(
        package,
        "helpers.py",
        'from .trusted.effects import record\ndef run() -> None:\n    record("one")\n',
    )
    source = _entry("helpers")

    calls = _calls(source, package)
    report = _coverage(source, package)

    assert "record" in calls
    assert DYNAMIC_CALL not in calls
    assert [entry["domain"] for entry in report] == ["record"]
    assert not marker.exists()


def test_ambient_sys_path_does_not_extend_source_resolution_roots(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside_root = package.parent / "ambient"
    _write(
        outside_root,
        "ambient_helper.py",
        "def run() -> None:\n    record()\n",
    )
    monkeypatch.syspath_prepend(str(outside_root))
    source = "from ambient_helper import run\ndef main() -> None:\n    run()\n"

    calls = _calls(source, package)

    assert DYNAMIC_CALL in calls
    assert "record" not in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == [
        "record",
        "audit",
    ]


@pytest.mark.parametrize(
    "problem", ["missing-module", "missing-export", "invalid-source"]
)
def test_unresolved_import_keeps_all_policy_candidates(
    package: Path, problem: str
) -> None:
    if problem == "missing-export":
        _write(package, "helpers.py", "def unrelated() -> None:\n    pass\n")
    elif problem == "invalid-source":
        _write(package, "helpers.py", "def run(\n")
    source = _entry("helpers")

    calls = _calls(source, package)

    assert DYNAMIC_CALL in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == [
        "record",
        "audit",
    ]


def test_symlink_outside_configured_roots_is_not_followed(package: Path) -> None:
    allowed = package.parent / "allowed"
    allowed_package = allowed / package.name
    _write(allowed_package, "__init__.py", "")
    outside = _write(
        package.parent,
        "outside_helper.py",
        "def run() -> None:\n    record()\n",
    )
    (allowed_package / "helpers.py").symlink_to(outside)

    calls = _calls(_entry("helpers"), allowed_package)

    assert DYNAMIC_CALL in calls
    assert "record" not in calls


def test_unused_imported_helper_does_not_trigger_coverage(package: Path) -> None:
    _write(
        package,
        "helpers.py",
        'from .trusted.effects import record\ndef run() -> None:\n    record("one")\n',
    )
    source = "from sample_policy.helpers import run\ndef main() -> None:\n    pass\n"

    assert _calls(source, package) == set()
    assert _coverage(source, package) == []


def test_trusted_effect_body_is_a_policy_traversal_boundary(package: Path) -> None:
    _write(
        package,
        "trusted/effects.py",
        "import clauz3\n@clauz3.trusted\ndef record() -> None:\n    audit()\n",
    )
    source = _entry("trusted.effects", "record")

    calls = _calls(source, package)

    assert "record" in calls
    assert "audit" not in calls
    assert DYNAMIC_CALL not in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == ["record"]


def test_trusted_decorator_outside_trusted_root_does_not_hide_helper_body(
    package: Path,
) -> None:
    _write(
        package,
        "helpers.py",
        "import clauz3\n"
        "from .trusted.effects import record\n"
        "@clauz3.trusted\n"
        'def run() -> None:\n    record("one")\n',
    )

    calls = _calls(_entry("helpers"), package)

    assert "record" in calls
    assert DYNAMIC_CALL not in calls


def test_omitting_resolution_roots_conservatively_keeps_imported_calls(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(package, "helpers.py", "def run() -> None:\n    pass\n")
    monkeypatch.syspath_prepend(str(package.parent))
    source = _entry("helpers")

    assert DYNAMIC_CALL in transitive_calls(source, "main")
    report = compute_coverage(source=source, guarantees=[], policies=_POLICIES)
    assert [entry["domain"] for entry in report] == ["record", "audit"]


@pytest.mark.parametrize(
    ("statement", "callee"),
    [
        ("import sample_policy.helpers as h", "h.run"),
        ("from sample_policy import helpers", "helpers.run"),
        ("import sample_policy", "sample_policy.helpers.run"),
    ],
)
def test_module_qualified_imported_helpers_reach_effect(
    package: Path, statement: str, callee: str
) -> None:
    _write(package, "__init__.py", "from . import helpers\n")
    _write(
        package,
        "helpers.py",
        'from .trusted.effects import record\ndef run() -> None:\n    record("one")\n',
    )
    source = f"{statement}\ndef main() -> None:\n    {callee}()\n"

    calls = _calls(source, package)

    assert "record" in calls
    assert DYNAMIC_CALL not in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == ["record"]


def test_namespace_package_parent_preserves_helper_resolution(package: Path) -> None:
    (package / "__init__.py").unlink()
    _write(
        package,
        "helpers.py",
        'from .trusted.effects import record\ndef run() -> None:\n    record("one")\n',
    )

    calls = _calls(_entry("helpers"), package)

    assert "record" in calls
    assert DYNAMIC_CALL not in calls


@pytest.mark.parametrize("first_binding", ["package", "module"])
def test_earlier_regular_parent_blocks_submodule_from_later_root(
    package: Path, first_binding: str
) -> None:
    earlier_root = package.parent / "earlier"
    name = (
        "sample_policy/__init__.py"
        if first_binding == "package"
        else "sample_policy.py"
    )
    _write(earlier_root, name, "")
    _write(
        package,
        "helpers.py",
        'from .trusted.effects import record\ndef run() -> None:\n    record("one")\n',
    )

    calls = transitive_calls(
        _entry("helpers"),
        "main",
        import_roots=[earlier_root, package.parent],
        trusted_roots=[package / "trusted"],
    )

    assert DYNAMIC_CALL in calls
    assert "record" not in calls


@pytest.mark.parametrize(
    "unsupported_source",
    [
        "from .trusted.effects import *\n",
        "from . import trusted\ntrusted.alias = 1\n",
    ],
    ids=["star-import", "attribute-assignment"],
)
def test_unsupported_imported_source_does_not_invoke_astroid_import_or_inference(
    package: Path, monkeypatch: pytest.MonkeyPatch, unsupported_source: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("source-only resolution must not import or infer")

    monkeypatch.setattr(nodes.NodeNG, "infer", forbidden)
    monkeypatch.setattr(nodes.Import, "do_import_module", forbidden)
    monkeypatch.setattr(nodes.ImportFrom, "do_import_module", forbidden)
    _write(package, "helpers.py", unsupported_source + "def run() -> None:\n    pass\n")
    source = _entry("helpers")

    calls = _calls(source, package)

    assert DYNAMIC_CALL in calls
    assert [entry["domain"] for entry in _coverage(source, package)] == [
        "record",
        "audit",
    ]


def test_named_imported_source_does_not_pollute_shared_module_caches(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        package,
        "helpers.py",
        'from .trusted.effects import record\ndef run() -> None:\n    record("one")\n',
    )
    # Warm up builtins before capturing identity: its first-use initialization
    # is separate from whether parsing imported source replaces the live cache.
    astroid.parse("pass")
    cache = AstroidManager().astroid_cache
    builtins = cache["builtins"]
    sentinel = nodes.Module("sample_policy.helpers")
    monkeypatch.setitem(cache, "sample_policy.helpers", sentinel)
    before_astroid = {
        name: module
        for name, module in cache.items()
        if name.startswith("sample_policy")
    }
    before_python = {
        name: module
        for name, module in sys.modules.items()
        if name.startswith("sample_policy")
    }

    calls = _calls(_entry("helpers"), package)

    assert "record" in calls
    assert DYNAMIC_CALL not in calls
    assert AstroidManager().astroid_cache is cache
    assert astroid.MANAGER.astroid_cache is cache
    assert AstroidManager.brain["astroid_cache"] is cache
    assert astroid.MANAGER.astroid_cache["builtins"] is builtins
    assert {
        name: module
        for name, module in cache.items()
        if name.startswith("sample_policy")
    } == before_astroid
    assert {
        name: module
        for name, module in sys.modules.items()
        if name.startswith("sample_policy")
    } == before_python


@pytest.mark.parametrize(
    ("encoding", "preamble", "message"),
    [("utf-8", "", "Ω café"), ("latin-1", "# coding: latin-1\n", "café")],
    ids=["default-utf8", "latin1-cookie"],
)
def test_imported_source_uses_python_encoding(
    package: Path, encoding: str, preamble: str, message: str
) -> None:
    source = (
        preamble
        + "from .trusted.effects import record\n"
        + f"def run() -> None:\n    record({message!r})\n"
    )
    (package / "helpers.py").write_bytes(source.encode(encoding))
    entry = _entry("helpers")

    calls = _calls(entry, package)

    assert {"run", "record"} <= calls
    assert DYNAMIC_CALL not in calls
    assert [domain["domain"] for domain in _coverage(entry, package)] == ["record"]


@pytest.mark.parametrize("error_type", [ValueError, RecursionError])
def test_imported_parse_error_uses_conservative_policy_fallback(
    package: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    _write(package, "helpers.py", "def run() -> None:\n    pass\n")
    original_parse = policy_imports.parse_source

    def reject_helper(
        source: str, name: str = "", path: str | None = None
    ) -> nodes.Module | None:
        if name == "sample_policy.helpers":
            raise error_type("imported source cannot be parsed")
        return original_parse(source, name, path)

    # Parser failure types vary by Python version and input size. Pin both
    # refusal paths without allocating a pathological AST in the test process.
    monkeypatch.setattr(policy_imports, "parse_source", reject_helper)
    entry = _entry("helpers")

    assert DYNAMIC_CALL in _calls(entry, package)
    assert [domain["domain"] for domain in _coverage(entry, package)] == [
        "record",
        "audit",
    ]


def test_imported_source_with_nul_uses_conservative_policy_fallback(
    package: Path,
) -> None:
    (package / "helpers.py").write_bytes(b"def run():\n    pass\n\x00")
    entry = _entry("helpers")

    assert DYNAMIC_CALL in _calls(entry, package)
    assert [domain["domain"] for domain in _coverage(entry, package)] == [
        "record",
        "audit",
    ]
