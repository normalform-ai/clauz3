"""Bundled layer dependencies retain identity without becoming engine state."""

import importlib
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text


def _evict(name: str) -> None:
    for module in tuple(sys.modules):
        if module == name or module.startswith(name + "."):
            del sys.modules[module]


def _bundled_dependent_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, str, str]:
    """Two bundled layers, one whose contracts build on the other's.

    No bundled stdlib library imports another, so this writes a pair into a
    temporary framework: ``tool``'s contracts apply ``dependency``'s path rule
    to ``tool``'s own effect. Only ``tool`` is a trusted root; ``dependency``
    is a retained registered-contract import, not an effect boundary.
    """
    framework = tmp_path / "framework"
    bundle = framework / "stdlib/lib"
    suffix = uuid4().hex
    tool, dependency = f"tool_{suffix}", f"dependency_{suffix}"
    for package in (tool, dependency):
        (bundle / package / "trusted").mkdir(parents=True)
        (bundle / package / "trusted/__init__.py").write_text("")
    (bundle / dependency / "trusted/contracts.py").write_text(
        "from clauz3.spec import ContractSpec, EffectRelation, contract\n"
        "from clauz3.spec import no_guarantees as core_no_guarantees\n"
        "def reads_under(relation: EffectRelation, root: str) -> ContractSpec:\n"
        "    return relation.all(lambda e: e.path.startswith(root))\n"
        "@contract\n"
        "def no_guarantees() -> ContractSpec:\n"
        "    return core_no_guarantees()\n"
    )
    (bundle / tool / "trusted/effects.py").write_text(
        "import clauz3\n"
        "@clauz3.trusted\n"
        "def search(pattern: str, path: str) -> list[str]:\n"
        "    return []\n"
    )
    (bundle / tool / "trusted/contracts.py").write_text(
        f"from {dependency}.trusted import contracts as dependency\n"
        f"from {tool}.trusted.effects import search\n"
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "Search = effect(search)\n"
        "@contract\n"
        "def only_read_under(root: str) -> ContractSpec:\n"
        "    return dependency.reads_under(Search, root)\n"
    )
    monkeypatch.setattr("clauz3.layer_state._FRAMEWORK", framework)
    return bundle, bundle / tool / "trusted", tool, dependency


@pytest.mark.parametrize(
    ("allowed", "preloaded"),
    [(True, False), (False, False), (True, True)],
)
def test_bundled_contract_dependency_survives_same_image_cache_eviction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allowed: bool,
    preloaded: bool,
) -> None:
    bundle, trusted, tool, dependency = _bundled_dependent_layer(tmp_path, monkeypatch)
    path = "/repo/a.py" if allowed else "/outside/a.py"
    source = (
        "import clauz3\n"
        f"from {tool}.trusted import contracts as rules\n"
        f"from {tool}.trusted.effects import search\n"
        "@clauz3.guarantee(rules.only_read_under('/repo'))\n"
        "def main():\n"
        f"    search('TODO', {path!r})\n"
    )
    expected = Conclusion.OK if allowed else Conclusion.FAIL
    roots = [trusted]
    paths = [bundle, tmp_path]
    if preloaded:
        with monkeypatch.context() as imports:
            imports.syspath_prepend(str(bundle))
            importlib.import_module(f"{dependency}.trusted.contracts")
    assert [
        result.proof.conclusion
        for result in prove_text(source, trusted_roots=roots, import_paths=paths)
    ] == [expected]
    contracts = importlib.import_module(f"{dependency}.trusted.contracts")
    dependency_parent = sys.modules[f"{dependency}.trusted"]
    rule = contracts.reads_under
    assert contracts.__file__ is not None
    assert (
        Path(contracts.__file__).resolve()
        == (bundle / dependency / "trusted/contracts.py").resolve()
    )

    # Match host cleanup without clearing the coordinator's retained image.
    _evict(tool)
    _evict(dependency)
    assert [
        result.proof.conclusion
        for result in prove_text(source, trusted_roots=roots, import_paths=paths)
    ] == [expected]
    assert sys.modules[f"{dependency}.trusted.contracts"] is contracts
    assert sys.modules[f"{dependency}.trusted"] is dependency_parent
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
