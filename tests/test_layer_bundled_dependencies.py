"""Bundled layer dependencies retain identity without becoming engine state."""

import importlib
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text

STDLIB = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"


def _evict(name: str) -> None:
    for module in tuple(sys.modules):
        if module == name or module.startswith(name + "."):
            del sys.modules[module]


@pytest.mark.parametrize(
    ("domain", "allowed", "preloaded"),
    [
        ("grep", True, False),
        ("find", True, False),
        ("grep", False, False),
        ("find", False, False),
        ("grep", True, True),
    ],
)
def test_bundled_contract_dependency_survives_same_image_cache_eviction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    domain: str,
    allowed: bool,
    preloaded: bool,
) -> None:
    tool = STDLIB / domain
    filesystem = STDLIB / "filesystem"
    path = "/repo/a.py" if allowed else "/outside/a.py"
    arguments = f"'TODO', {path!r}" if domain == "grep" else f"{path!r}, []"
    source = (
        "import clauz3\n"
        f"from tools.{domain}.trusted import contracts as rules\n"
        f"from tools.{domain}.trusted.effects import {domain}\n"
        "@clauz3.guarantee(rules.only_read_under('/repo'))\n"
        "def main():\n"
        f"    {domain}({arguments})\n"
    )
    expected = Conclusion.OK if allowed else Conclusion.FAIL
    roots = [tool / f"tools/{domain}/trusted"]
    paths = [tool, filesystem, tmp_path]
    if preloaded:
        with monkeypatch.context() as imports:
            imports.syspath_prepend(str(filesystem))
            importlib.import_module("tools.filesystem.trusted.contracts")
    assert [
        result.proof.conclusion
        for result in prove_text(source, trusted_roots=roots, import_paths=paths)
    ] == [expected]
    contracts = importlib.import_module("tools.filesystem.trusted.contracts")
    dependency_parent = sys.modules["tools.filesystem.trusted"]
    rule = contracts.reads_under
    assert contracts.__file__ is not None
    assert (
        Path(contracts.__file__).resolve()
        == (filesystem / "tools/filesystem/trusted/contracts.py").resolve()
    )

    # Match host cleanup without clearing the coordinator's retained image.
    _evict("tools")
    assert [
        result.proof.conclusion
        for result in prove_text(source, trusted_roots=roots, import_paths=paths)
    ] == [expected]
    assert sys.modules["tools.filesystem.trusted.contracts"] is contracts
    assert sys.modules["tools.filesystem.trusted"] is dependency_parent
    assert dependency_parent.contracts is contracts
    assert contracts.reads_under is rule


def _temporary_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, str, str]:
    # This fixture exercises the installed-package layout without modifying
    # the real bundled sources. Configured trusted roots still contain only
    # the tool; the helper is a retained dependency, not an effect boundary.
    framework = tmp_path / "framework"
    bundled = framework / "stdlib"
    tool_root, shared_root = bundled / "tool", bundled / "shared"
    name = f"bundled_{uuid4().hex}"
    tool = tool_root / name
    tool.mkdir(parents=True)
    (tool / "__init__.py").write_text("")
    shared_root.mkdir(parents=True)
    helper_name = f"{name}_helper"
    helper = shared_root / f"{helper_name}.py"
    helper.write_text("LIMIT = 1\n")
    (tool / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (tool / "contracts.py").write_text(
        f"import {helper_name} as helper\n"
        "from clauz3.spec import contract, effect\n"
        "@contract\n"
        "def budget():\n"
        "    return effect('record').count() <= helper.LIMIT\n"
    )
    source = (
        "import clauz3\n"
        f"from {name}.effects import record\n"
        f"from {name}.contracts import budget\n"
        "@clauz3.guarantee(budget())\n"
        "def main():\n"
        "    record('one')\n"
    )
    monkeypatch.setattr("clauz3.layer_state._FRAMEWORK", framework)
    return tool, shared_root, helper, helper_name, source


def test_bundled_nonregistered_dependency_source_change_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, shared, helper, name, source = _temporary_bundle(tmp_path, monkeypatch)
    paths = [tool.parent, shared]
    proofs = prove_text(source, trusted_roots=[tool], import_paths=paths)
    assert [result.proof.conclusion for result in proofs] == [Conclusion.OK]
    retained = sys.modules[name]
    original = helper.stat()
    helper.write_text("LIMIT = 0\n")
    os.utime(helper, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ProverConfigError, match="layer source changed"):
        prove_text(source, trusted_roots=[tool], import_paths=paths)
    assert sys.modules[name] is retained
    assert sys.modules[f"{tool.name}.effects"].EVENTS == []


def test_preloaded_bundled_dependency_with_stale_transitive_source_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, shared, helper, helper_name, source = _temporary_bundle(tmp_path, monkeypatch)
    authority = f"authority_{uuid4().hex}"
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / f"{authority}.py").write_text("LIMIT = 1\n")
    (second / f"{authority}.py").write_text("LIMIT = 0\n")
    helper.write_text(f"import {authority}\nLIMIT = {authority}.LIMIT\n")
    with monkeypatch.context() as imports:
        imports.syspath_prepend(str(first))
        imports.syspath_prepend(str(shared))
        retained = importlib.import_module(helper_name)
    with pytest.raises(ProverConfigError, match="cached layer dependency"):
        prove_text(
            source, trusted_roots=[tool], import_paths=[tool.parent, shared, second]
        )
    assert sys.modules[helper_name] is retained
