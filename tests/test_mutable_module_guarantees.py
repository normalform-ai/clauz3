"""Mutable trusted-module state cannot be replaced by its initial literal."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, run_source

MUTATIONS = (
    pytest.param(
        'ALLOWED = ["bob"]\nALLOWED.append("eve")\n',
        '"eve" in effects.ALLOWED',
        "",
        id="direct-mutation",
    ),
    pytest.param(
        'ALLOWED = ["bob"]\nalias = ALLOWED\nalias.append("eve")\n',
        '"eve" in effects.ALLOWED',
        "",
        id="alias-mutation",
    ),
    pytest.param(
        'ALLOWED = ["bob"]\n'
        "@clauz3.trusted\n"
        "def prepare() -> None:\n"
        '    ALLOWED.append("eve")\n'
        "prepare()\n",
        '"eve" in effects.ALLOWED',
        "",
        id="trusted-helper-mutation",
    ),
    pytest.param(
        'ALLOWED = (["bob"],)\nALLOWED[0].append("eve")\n',
        '"eve" in effects.ALLOWED[0]',
        "",
        id="mutable-element-in-tuple",
    ),
    pytest.param(
        'ALLOWED = ["bob"]\n'
        "@clauz3.trusted\n"
        "def mutate() -> None:\n"
        '    ALLOWED.append("eve")\n',
        '"eve" in effects.ALLOWED',
        "    mutate()\n",
        id="trusted-helper-during-entry",
    ),
)


def _program(
    tmp_path: Path, initializer: str, guard: str, setup: str = ""
) -> tuple[str, Path]:
    package = tmp_path / f"module_mutation_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "EVENTS = []\n" + initializer + "\n@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        '    return effect("record").empty()\n'
    )
    extra_import = f"from {package.name}.effects import mutate\n" if setup else ""
    source = (
        "import clauz3\n"
        f"from {package.name} import effects\n"
        f"from {package.name}.effects import record\n"
        f"{extra_import}"
        f"from {package.name}.contracts import silent\n\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        f"{setup}"
        f"    if {guard}:\n"
        '        record("one")\n'
    )
    return source, package


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


@pytest.mark.parametrize(("initializer", "guard", "setup"), MUTATIONS)
def test_mutated_module_value_cannot_prove_a_false_silent_guarantee(
    tmp_path: Path, initializer: str, guard: str, setup: str
) -> None:
    source, package = _program(tmp_path, initializer, guard, setup)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "mutable or non-literal module attribute" in str(proofs[0].proof)


@pytest.mark.parametrize(("initializer", "guard", "setup"), MUTATIONS)
def test_mutated_module_value_stops_run_before_approval_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initializer: str,
    guard: str,
    setup: str,
) -> None:
    source, package = _program(tmp_path, initializer, guard, setup)
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    execute_source = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute_source)

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
    assert "mutable or non-literal module attribute" in str(
        exc_info.value.proofs[0].proof
    )
    approve.assert_not_called()
    execute_source.assert_not_called()


@pytest.mark.parametrize(("initializer", "guard", "setup"), MUTATIONS)
def test_interpreter_observes_the_mutated_module_and_records_an_effect(
    tmp_path: Path, initializer: str, guard: str, setup: str
) -> None:
    source, package = _program(tmp_path, initializer, guard, setup)

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )

    expected_effects: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = (
        (f"{package.name}.effects.record", ("one",), {}),
    )
    if setup:
        expected_effects = (
            (f"{package.name}.effects.mutate", (), {}),
        ) + expected_effects
    assert outcome.effects == expected_effects
    effects = importlib.import_module(f"{package.name}.effects")
    recorded = effects.EVENTS
    assert recorded == ["one"]


@pytest.mark.parametrize(
    ("initializer", "guard", "expected_events"),
    [
        pytest.param(
            'ALLOWED = ("bob", "eve")\n',
            '"eve" in effects.ALLOWED',
            ["one"],
            id="immutable-tuple-used",
        ),
        pytest.param(
            'ALLOWED = "eve"\n',
            'effects.ALLOWED == "eve"',
            ["one"],
            id="immutable-scalar-used",
        ),
        pytest.param(
            'ALLOWED = ("bob",)\n',
            '"eve" in effects.ALLOWED',
            [],
            id="immutable-tuple-unused",
        ),
    ],
)
def test_immutable_module_values_are_refused_even_when_they_stay_unchanged(
    tmp_path: Path,
    initializer: str,
    guard: str,
    expected_events: list[str],
) -> None:
    source, package = _program(tmp_path, initializer, guard)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "module data bindings are not modeled" in str(proofs[0].proof)

    execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    recorded = effects.EVENTS
    assert recorded == expected_events
