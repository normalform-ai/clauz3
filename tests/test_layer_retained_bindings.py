"""Cached trusted imports must retain the objects selected for the proof."""

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.client import ApprovalResponse
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import ProverConfigError, prove_text
from clauz3.runner import RunConfigError, run_source


@dataclass(frozen=True)
class _Case:
    layer: Path
    paths: list[Path]
    source: str
    effects: ModuleType
    original: ModuleType
    selected: ModuleType


def _case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial: str,
    selected: str,
    binding: str = "callable",
    dead_alias: bool = False,
    same_bound: str | None = None,
) -> _Case:
    name = f"retained_tools_{uuid4().hex}"
    authority = f"retained_authority_{uuid4().hex}"
    provider = f"retained_provider_{uuid4().hex}"
    exported = {"scalar": "DECISION", "class": "Decision"}.get(binding, "decision")
    value = {"scalar": "decision", "class": "decision.VALUE"}.get(binding, "decision()")
    layer = tmp_path / "common" / name
    layer.mkdir(parents=True)
    (layer / "__init__.py").write_text("")
    unrelated = (
        f"from {provider}_A import decision as unrelated\n"
        if binding == "split-reexport"
        else ""
    )
    earlier = f"from {provider}_A import decision\n" if same_bound == "earlier" else ""
    dead_import = (
        f"if False:\n    from {provider}_A import decision\n"
        if same_bound == "dead"
        else ""
    )
    (layer / "effects.py").write_text(
        "import clauz3\n"
        + earlier
        + f"from {authority} import {exported} as decision\n"
        + unrelated
        + "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        f"@clauz3.ensures(lambda result: result == {value})\n"
        "def allowed() -> bool:\n"
        f"    return {value}\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        + ("if False:\n    decision = unrelated\n" if dead_alias else "")
        + dead_import
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    for label, decision in (("A", True), ("B", False)):
        directory = tmp_path / label
        directory.mkdir()
        helper_source = f"def decision() -> bool:\n    return {decision!r}\n"
        if binding == "scalar":
            helper_source = f"DECISION = {decision!r}\n"
        elif binding == "class":
            helper_source = f"class Decision:\n    VALUE = {decision!r}\n"
        elif binding == "reexport":
            (directory / f"{provider}.py").write_text(helper_source)
            helper_source = f"from {provider} import decision\n"
        elif binding == "split-reexport":
            # Both defining modules remain selected and coherent. Only the
            # authority re-export changes; an unrelated valid alias must not
            # authorize the old imported decision.
            (layer.parent / f"{provider}_{label}.py").write_text(helper_source)
            helper_source = f"from {provider}_{label} import decision\n"
        elif binding == "wrapped":
            helper_source = (
                "from functools import wraps\n"
                "def wrap(func):\n"
                "    @wraps(func)\n"
                "    def wrapped() -> bool:\n"
                "        return func()\n"
                "    return wrapped\n"
                "@wrap\n" + helper_source
            )
        (directory / f"{authority}.py").write_text(helper_source)
    source = (
        "import clauz3\n"
        f"from {name}.effects import allowed, record\n"
        f"from {name}.contracts import silent\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        "    if allowed():\n"
        "        record('bad')\n"
    )
    for module_name in (
        name,
        f"{name}.effects",
        f"{name}.contracts",
        authority,
        provider,
        f"{provider}_A",
        f"{provider}_B",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    with monkeypatch.context() as imports:
        imports.setattr(
            sys, "path", [str(tmp_path / initial), str(layer.parent), *sys.path]
        )
        effects = importlib.import_module(f"{name}.effects")
    original = sys.modules[authority]
    if initial != selected:
        # Keep the genuine trusted module and its imported function alive while
        # replacing only the helper's sys.modules entry before the first proof.
        monkeypatch.delitem(sys.modules, authority)
        if binding == "reexport":
            monkeypatch.delitem(sys.modules, provider)
        with monkeypatch.context() as imports:
            imports.setattr(
                sys, "path", [str(tmp_path / selected), str(layer.parent), *sys.path]
            )
            importlib.import_module(authority)
    return _Case(
        layer=layer,
        paths=[tmp_path / selected, layer.parent],
        source=source,
        effects=effects,
        original=original,
        selected=sys.modules[authority],
    )


@pytest.mark.parametrize("operation", ["prove", "run", "prove_claimed_filename"])
def test_retained_callable_refused_before_proof_or_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    case = _case(tmp_path, monkeypatch, initial="A", selected="B")
    assert case.effects.decision is case.original.decision
    assert case.effects.decision is not case.selected.decision
    assert case.effects.decision.__globals__ is vars(case.original)
    assert case.effects.decision() is True
    assert case.selected.decision() is False
    if operation == "prove_claimed_filename":
        # A selected-looking filename cannot attest the retained globals owner.
        case.effects.decision.__code__ = case.effects.decision.__code__.replace(
            co_filename=str(case.selected.__file__)
        )
        assert case.effects.decision.__globals__ is vars(case.original)
        assert case.effects.decision() is True
    approval = Mock(return_value=ApprovalResponse(decision="denied"))
    executor = Mock(side_effect=AssertionError("execution must not begin"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)

    if operation != "run":
        with pytest.raises(ProverConfigError, match="cached layer binding"):
            prove_text(case.source, trusted_roots=[case.layer], import_paths=case.paths)
    else:
        with pytest.raises(RunConfigError, match="cached layer binding"):
            run_source(
                case.source,
                source_name="entry.py",
                trusted_roots=[case.layer],
                import_roots=case.paths,
                approval_service_url="http://approval.invalid",
            )

    approval.assert_not_called()
    executor.assert_not_called()
    assert case.effects.EVENTS == []
    assert sys.modules[case.effects.__name__] is case.effects
    assert sys.modules[case.selected.__name__] is case.selected


@pytest.mark.parametrize(
    ("label", "conclusion", "events"),
    [("A", Conclusion.FAIL, ["bad"]), ("B", Conclusion.OK, [])],
)
def test_matching_callable_proof_agrees_with_native_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    conclusion: Conclusion,
    events: list[str],
) -> None:
    case = _case(tmp_path, monkeypatch, initial=label, selected=label)
    assert case.effects.decision is case.selected.decision
    proofs = prove_text(
        case.source, trusted_roots=[case.layer], import_paths=case.paths
    )
    assert [proof.proof.conclusion for proof in proofs] == [conclusion]
    assert case.effects.EVENTS == []

    outcome = execute(
        case.source,
        source_name="entry.py",
        policy=build_policy(trusted_roots=[case.layer], import_roots=case.paths),
        import_roots=case.paths,
    )

    assert events == case.effects.EVENTS
    assert [effect[1] for effect in outcome.effects] == [(), *[(v,) for v in events]]
    assert sys.modules[case.effects.__name__] is case.effects


def test_matching_decorated_helper_retains_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path, monkeypatch, initial="B", selected="B", binding="wrapped")
    wrapper = case.effects.decision
    assert wrapper is case.selected.decision
    assert wrapper is not wrapper.__wrapped__
    assert wrapper.__globals__ is vars(case.selected)
    assert wrapper.__wrapped__.__globals__ is vars(case.selected)
    proofs = prove_text(
        "def main() -> None:\n    assert True\n",
        trusted_roots=[case.layer],
        import_paths=case.paths,
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert case.effects.decision is wrapper
    assert case.effects.allowed() is False
    assert case.effects.EVENTS == []


def test_wrapped_callable_cycle_refused_before_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path, monkeypatch, initial="B", selected="B", binding="wrapped")
    case.effects.decision.__wrapped__ = case.effects.decision

    with pytest.raises(ProverConfigError, match="cached layer binding"):
        prove_text(
            "def main() -> None:\n    assert True\n",
            trusted_roots=[case.layer],
            import_paths=case.paths,
        )

    assert case.effects.EVENTS == []


@pytest.mark.parametrize("binding", ["scalar", "class", "reexport", "wrapped"])
def test_retained_import_alias_refused_before_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    case = _case(tmp_path, monkeypatch, initial="A", selected="B", binding=binding)
    exported = {"scalar": "DECISION", "class": "Decision"}.get(binding, "decision")
    assert case.effects.decision is vars(case.original)[exported]
    assert case.effects.decision is not vars(case.selected)[exported]

    with pytest.raises(ProverConfigError, match="cached layer binding"):
        prove_text(
            "def main() -> None:\n    assert True\n",
            trusted_roots=[case.layer],
            import_paths=case.paths,
        )

    assert case.effects.EVENTS == []
    assert sys.modules[case.effects.__name__] is case.effects
    assert sys.modules[case.selected.__name__] is case.selected


@dataclass(frozen=True)
class _StarCase:
    layer: Path
    paths: list[Path]
    effects_name: str
    authority: str
    source: str


def _star_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    selected: str,
    dropped_export: bool = False,
    preload: bool = False,
) -> _StarCase:
    name = f"star_tools_{uuid4().hex}"
    authority = f"star_authority_{uuid4().hex}"
    layer = tmp_path / "common" / name
    layer.mkdir(parents=True)
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text(
        "import clauz3\n"
        "FLAG = False\n"
        f"from {authority} import *\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def allowed() -> bool:\n"
        "    return FLAG\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def once() -> ContractSpec:\n"
        "    return effect('record').count() <= 1\n"
    )
    for label in ("A", "B"):
        directory = tmp_path / label
        directory.mkdir()
        names = [] if label == "B" and dropped_export else ["FLAG"]
        (directory / f"{authority}.py").write_text(
            f"__all__ = {names!r}\nFLAG = {label == 'A'!r}\n"
        )
    effects_name = f"{name}.effects"
    for module_name in (name, effects_name, f"{name}.contracts", authority):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    if preload:
        with monkeypatch.context() as imports:
            imports.setattr(
                sys, "path", [str(tmp_path / "A"), str(layer.parent), *sys.path]
            )
            importlib.import_module(effects_name)
        if selected != "A":
            monkeypatch.delitem(sys.modules, authority)
            with monkeypatch.context() as imports:
                imports.syspath_prepend(str(tmp_path / selected))
                importlib.import_module(authority)
    return _StarCase(
        layer,
        [tmp_path / selected, layer.parent],
        effects_name,
        authority,
        "import clauz3\n"
        f"from {name}.effects import record\n"
        f"from {name}.contracts import once\n"
        "@clauz3.guarantee(once())\n"
        "def main() -> None:\n"
        "    record('one')\n",
    )


@pytest.mark.parametrize(
    ("selected", "dropped_export"), [("A", False), ("B", False), ("B", True)]
)
def test_ambient_star_import_has_no_attested_export_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
    dropped_export: bool,
) -> None:
    case = _star_case(
        tmp_path,
        monkeypatch,
        selected=selected,
        dropped_export=dropped_export,
        preload=True,
    )
    effects = sys.modules[case.effects_name]
    assert effects.allowed() is True
    if dropped_export:
        assert sys.modules[case.authority].__all__ == []
    with pytest.raises(ProverConfigError, match="cached layer star binding"):
        prove_text(
            "def main() -> None:\n    assert True\n",
            trusted_roots=[case.layer],
            import_paths=case.paths,
        )
    assert effects.EVENTS == []
    assert sys.modules[case.effects_name] is effects


def test_changed_star_export_set_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _star_case(
        tmp_path, monkeypatch, selected="B", dropped_export=True, preload=True
    )
    approval = Mock(side_effect=AssertionError("approval must not begin"))
    executor = Mock(side_effect=AssertionError("execution must not begin"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunConfigError, match="cached layer star binding"):
        run_source(
            case.source,
            source_name="entry.py",
            trusted_roots=[case.layer],
            import_roots=case.paths,
            approval_service_url="http://approval.invalid",
        )
    approval.assert_not_called()
    executor.assert_not_called()
    assert sys.modules[case.effects_name].EVENTS == []


@pytest.mark.parametrize("label", ["A", "B"])
def test_fresh_star_import_keeps_verified_identity_across_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    case = _star_case(tmp_path, monkeypatch, selected=label)
    for _ in range(2):
        proofs = prove_text(
            case.source, trusted_roots=[case.layer], import_paths=case.paths
        )
        assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    effects = sys.modules[case.effects_name]
    assert effects.allowed() is (label == "A")
    outcome = execute(
        case.source,
        source_name="entry.py",
        policy=build_policy(trusted_roots=[case.layer], import_roots=case.paths),
        import_roots=case.paths,
    )
    assert [effect[1] for effect in outcome.effects] == [("one",)]
    assert effects.EVENTS == ["one"]
    assert sys.modules[case.effects_name] is effects


def test_verified_star_export_history_is_rechecked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _star_case(tmp_path, monkeypatch, selected="A")
    proofs = prove_text(
        case.source, trusted_roots=[case.layer], import_paths=case.paths
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    effects = sys.modules[case.effects_name]
    monkeypatch.setattr(sys.modules[case.authority], "__all__", [])
    with pytest.raises(ProverConfigError, match="cached layer star binding changed"):
        prove_text(case.source, trusted_roots=[case.layer], import_paths=case.paths)
    assert effects.EVENTS == []


def test_failed_activation_does_not_attest_star_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauz3.layer_state import LayerStateError, selected_layer

    case = _star_case(tmp_path, monkeypatch, selected="A")
    with (
        pytest.raises(RuntimeError, match="abort after checked imports"),
        selected_layer(trusted_roots=[case.layer], import_paths=case.paths) as image,
    ):
        importlib.import_module(case.effects_name)
        image.refresh_registrations()
        raise RuntimeError("abort after checked imports")
    with (
        pytest.raises(LayerStateError, match="cached layer star binding"),
        image.activate(),
    ):
        image.refresh_registrations()


@pytest.mark.parametrize("dead_alias", [False, True])
def test_unrelated_verified_alias_cannot_attest_stale_reexport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dead_alias: bool
) -> None:
    case = _case(
        tmp_path,
        monkeypatch,
        initial="A",
        selected="B",
        binding="split-reexport",
        dead_alias=dead_alias,
    )
    assert case.effects.decision is case.effects.unrelated
    assert case.effects.decision() is True
    assert case.selected.decision() is False
    original_owner = sys.modules[case.effects.decision.__module__]
    assert case.effects.decision.__globals__ is vars(original_owner)
    assert original_owner.decision is case.effects.decision
    with pytest.raises(ProverConfigError, match="cached layer binding"):
        prove_text(case.source, trusted_roots=[case.layer], import_paths=case.paths)
    assert case.effects.EVENTS == []


def _fresh_alias_layer(tmp_path: Path) -> Path:
    layer = tmp_path / f"fresh_alias_{uuid4().hex}"
    layer.mkdir()
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text(
        "import clauz3\nEVENTS = []\n"
        "@clauz3.trusted\n"
        "def match(pattern: str, value: str) -> None:\n"
        "    EVENTS.append((pattern, value))\n"
    )
    (layer / "selection.py").write_text(
        "from .effects import match as actual\n"
        "from re import match\n"
        "FLAG = True\n"
        "if FLAG:\n    match = globals()['actual']\n"
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n    return effect().empty()\n"
        "@contract\n"
        "def once() -> ContractSpec:\n    return effect().count() <= 1\n"
    )
    return layer


@pytest.mark.parametrize(
    ("module", "contract", "conclusion"),
    [
        ("effects", "once", Conclusion.OK),
        ("effects", "silent", Conclusion.FAIL),
        ("selection", "silent", Conclusion.SKIP),
    ],
)
def test_fresh_conditional_alias_keeps_checked_history_on_reuse(
    tmp_path: Path, module: str, contract: str, conclusion: Conclusion
) -> None:
    layer = _fresh_alias_layer(tmp_path)
    source = (
        "import clauz3\n"
        f"from {layer.name}.{module} import match\n"
        f"from {layer.name}.contracts import {contract}\n"
        f"@clauz3.guarantee({contract}())\n"
        "def main() -> None:\n    match('bob', 'bob')\n"
    )
    results = prove_text(source, trusted_roots=[layer], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in results] == [conclusion]
    selected = sys.modules[f"{layer.name}.selection"]
    effects = sys.modules[f"{layer.name}.effects"]
    assert selected.match is effects.match
    repeated = prove_text(source, trusted_roots=[layer], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in repeated] == [conclusion]
    if conclusion is Conclusion.SKIP:
        assert "callable target" in str(repeated[0].proof)
    assert sys.modules[selected.__name__] is selected
    assert effects.EVENTS == []


def test_failed_activation_does_not_attest_conditional_alias(
    tmp_path: Path,
) -> None:
    from clauz3.layer_state import LayerStateError, selected_layer

    layer = _fresh_alias_layer(tmp_path)
    with (
        pytest.raises(RuntimeError, match="abort checked alias"),
        selected_layer(trusted_roots=[layer], import_paths=[tmp_path]) as image,
    ):
        importlib.import_module(f"{layer.name}.selection")
        image.refresh_registrations()
        raise RuntimeError("abort checked alias")
    with (
        pytest.raises(LayerStateError, match="cached layer binding"),
        image.activate(),
    ):
        image.refresh_registrations()


@pytest.mark.parametrize("kind", ["star", "alias"])
def test_failed_parked_image_is_not_a_fresh_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from clauz3.layer_state import LayerStateError, selected_layer

    if kind == "star":
        case = _star_case(tmp_path, monkeypatch, selected="A")
        layer, paths_a = case.layer, case.paths
        paths_b = [tmp_path / "B", layer.parent]
        module_name = case.effects_name
    else:
        layer = _fresh_alias_layer(tmp_path)
        paths_a = [tmp_path]
        other = tmp_path / "other"
        other.mkdir()
        paths_b = [other, tmp_path]
        module_name = f"{layer.name}.selection"
    results = prove_text(
        "def main() -> None:\n    assert True\n",
        trusted_roots=[layer],
        import_paths=paths_a,
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    original = sys.modules[module_name]
    with (
        pytest.raises(RuntimeError, match="abort displaced image"),
        selected_layer(trusted_roots=[layer], import_paths=paths_b) as image,
    ):
        replacement = importlib.import_module(module_name)
        assert replacement is not original
        image.refresh_registrations()
        raise RuntimeError("abort displaced image")
    assert sys.modules[module_name] is original
    with (
        pytest.raises(LayerStateError, match="cached layer.*binding"),
        image.activate(),
    ):
        image.refresh_registrations()
    assert sys.modules[module_name] is original


@pytest.mark.parametrize("same_bound", ["earlier", "dead"])
def test_historical_same_name_import_cannot_attest_stale_reexport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_bound: str
) -> None:
    case = _case(
        tmp_path,
        monkeypatch,
        initial="A",
        selected="B",
        binding="split-reexport",
        same_bound=same_bound,
    )
    assert case.effects.decision is case.effects.unrelated
    assert case.effects.allowed() is True
    assert case.selected.decision() is False
    with pytest.raises(ProverConfigError, match="cached layer binding"):
        prove_text(case.source, trusted_roots=[case.layer], import_paths=case.paths)
    assert case.effects.EVENTS == []
