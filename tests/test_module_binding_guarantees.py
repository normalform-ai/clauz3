"""Source literals cannot stand in for the current value of module bindings."""

import importlib
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

DIAGNOSTIC = "module data bindings are not modeled"
CASES = [
    "initializer-globals",
    "sibling-module",
    "trusted-enable",
    "module-alias",
    "local-snapshot",
    "ordinary-helper",
    "immutable-tuple",
    "stable-false",
    "stable-true",
]


def _program(tmp_path: Path, case: str) -> tuple[str, Path]:
    package = tmp_path / f"module_binding_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    initializer = "FLAG = False\nglobals()['FLAG'] = True\n"
    suffix = ""
    if case == "sibling-module":
        initializer = "FLAG = False\n"
        suffix = "from . import rebinder\n"
        (package / "rebinder.py").write_text(
            "from . import effects\neffects.FLAG = True\n"
        )
    elif case == "trusted-enable":
        initializer = (
            "FLAG = False\n"
            "@clauz3.trusted\n"
            "def enable() -> None:\n"
            "    globals()['FLAG'] = True\n"
        )
    elif case == "immutable-tuple":
        initializer = "FLAG = (False,)\nglobals()['FLAG'] = (True,)\n"
    elif case in {"stable-false", "stable-true"}:
        initializer = f"FLAG = {case == 'stable-true'}\n"
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n" + initializer + "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n" + suffix
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    imports = f"from {package.name} import effects\n"
    helpers = ""
    setup = ""
    guard = "effects.FLAG"
    if case == "trusted-enable":
        imports += f"from {package.name}.effects import enable\n"
        setup = "enable()\n"
    elif case == "module-alias":
        imports = f"import {package.name}.effects as tools\n"
        guard = "tools.FLAG"
    elif case == "local-snapshot":
        setup = "snapshot = effects.FLAG\n"
        guard = "snapshot"
    elif case == "ordinary-helper":
        helpers = "def enabled() -> bool:\n    return effects.FLAG\n"
        guard = "enabled()"
    elif case == "immutable-tuple":
        guard = "effects.FLAG[0]"
    source = (
        "import clauz3\n"
        + imports
        + f"from {package.name}.effects import record\n"
        + f"from {package.name}.contracts import silent\n"
        + helpers
        + "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        + indent(setup + f"if {guard}:\n    record('one')\n", "    ")
    )
    return source, package


@pytest.mark.parametrize("case", CASES)
def test_module_binding_reads_stop_before_approval_and_keep_runtime_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source, package = _program(tmp_path, case)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert DIAGNOSTIC in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert effects.EVENTS == []
    if case == "trusted-enable":
        assert effects.FLAG is False

    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert DIAGNOSTIC in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []
    if case == "trusted-enable":
        assert effects.FLAG is False

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    expected_events = [] if case == "stable-false" else ["one"]
    recorded = effects.EVENTS
    assert recorded == expected_events
    expected_effects: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = ()
    if case == "trusted-enable":
        assert effects.FLAG is True
        expected_effects += ((f"{package.name}.effects.enable", (), {}),)
    if expected_events:
        expected_effects += ((f"{package.name}.effects.record", ("one",), {}),)
    assert outcome.effects == expected_effects
