"""A bytecode-only carrier cannot attest its retained copied imports."""

import importlib
import py_compile
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text
from clauz3.runner import RunConfigError, run_source

SOURCE = "def main() -> None:\n    assert True\n"


@dataclass(frozen=True)
class _Case:
    layer: Path
    paths: list[Path]
    effects: ModuleType
    carrier: ModuleType
    helper: ModuleType


def _case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bytecode_only: bool,
    stale: bool,
) -> _Case:
    name = f"sourceless_tools_{uuid4().hex}"
    helper_name = f"sourceless_helper_{uuid4().hex}"
    carrier_name = f"sourceless_carrier_{uuid4().hex}"
    layer = tmp_path / "common" / name
    layer.mkdir(parents=True)
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text(
        "import clauz3\n"
        f"import {carrier_name}\n"
        "EVENTS: list[bool] = []\n"
        "@clauz3.trusted\n"
        "def record() -> None:\n"
        f"    EVENTS.append({carrier_name}.FLAG)\n"
    )
    carrier_source = layer.parent / f"{carrier_name}.py"
    carrier_source.write_text(f"from {helper_name} import FLAG\n")
    if bytecode_only:
        py_compile.compile(
            str(carrier_source),
            cfile=str(carrier_source.with_suffix(".pyc")),
            doraise=True,
        )
        carrier_source.unlink()
    for label, value in (("A", True), ("B", False)):
        directory = tmp_path / label
        directory.mkdir()
        (directory / f"{helper_name}.py").write_text(f"FLAG = {value!r}\n")
    for module_name in (name, f"{name}.effects", carrier_name, helper_name):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    with monkeypatch.context() as imports:
        imports.setattr(
            sys, "path", [str(tmp_path / "A"), str(layer.parent), *sys.path]
        )
        effects = importlib.import_module(f"{name}.effects")
    carrier = sys.modules[carrier_name]
    selected = tmp_path / ("B" if stale else "A")
    if stale:
        monkeypatch.delitem(sys.modules, helper_name)
        with monkeypatch.context() as imports:
            imports.syspath_prepend(str(selected))
            importlib.import_module(helper_name)
    return _Case(
        layer=layer,
        paths=[selected, layer.parent],
        effects=effects,
        carrier=carrier,
        helper=sys.modules[helper_name],
    )


@pytest.mark.parametrize("stale", [False, True])
def test_cached_bytecode_carrier_refused_before_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale: bool
) -> None:
    case = _case(tmp_path, monkeypatch, bytecode_only=True, stale=stale)
    assert case.carrier.FLAG is True
    assert case.helper.FLAG is (not stale)

    with pytest.raises(ProverConfigError, match="cached layer.*source"):
        prove_text(SOURCE, trusted_roots=[case.layer], import_paths=case.paths)

    assert sys.modules[case.effects.__name__] is case.effects
    assert sys.modules[case.carrier.__name__] is case.carrier
    assert sys.modules[case.helper.__name__] is case.helper
    assert case.effects.EVENTS == []


def test_cached_bytecode_carrier_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path, monkeypatch, bytecode_only=True, stale=True)
    approval = Mock(side_effect=AssertionError("approval must not begin"))
    executor = Mock(side_effect=AssertionError("execution must not begin"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)

    with pytest.raises(RunConfigError, match="cached layer.*source"):
        run_source(
            SOURCE,
            source_name="entry.py",
            trusted_roots=[case.layer],
            import_roots=case.paths,
            approval_service_url="http://approval.invalid",
        )

    approval.assert_not_called()
    executor.assert_not_called()
    assert case.effects.EVENTS == []
    assert sys.modules[case.carrier.__name__] is case.carrier
    assert sys.modules[case.helper.__name__] is case.helper


@pytest.mark.parametrize("stale", [False, True])
def test_source_backed_carrier_requires_matching_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale: bool
) -> None:
    case = _case(tmp_path, monkeypatch, bytecode_only=False, stale=stale)
    if stale:
        with pytest.raises(ProverConfigError, match="cached layer binding"):
            prove_text(SOURCE, trusted_roots=[case.layer], import_paths=case.paths)
    else:
        proofs = prove_text(SOURCE, trusted_roots=[case.layer], import_paths=case.paths)
        assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert sys.modules[case.carrier.__name__] is case.carrier
    assert sys.modules[case.helper.__name__] is case.helper
    assert case.effects.EVENTS == []
