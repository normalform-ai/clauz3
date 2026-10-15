"""Policy discovery must resolve imports in the scope of each actual use."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.policy import DomainPolicy, compute_coverage, transitive_calls
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, build_approval_request, run_source


def _layer(tmp_path: Path, *, required: bool = True) -> Path:
    package = tmp_path / f"policy_alias_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "EVENTS = []\n\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        'Records = effect("record")\n\n'
        "@contract\n"
        "def unique_values() -> ContractSpec:\n"
        "    return Records.distinct(lambda e: e.value)\n"
    )
    field = "required" if required else "recommended"
    (package / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n"
        'POLICY = domain_policy(when_used="record", '
        f'{field}=["unique_values"], label="recording")\n'
    )
    return package


def _unused_import(scope: str, alias: str) -> str:
    statement = f"from re import match as {alias}\n"
    if scope == "class":
        return f"\nclass Unused:\n    {statement}"
    if scope == "nested-function":
        return (
            f"\ndef unused() -> None:\n    def nested() -> None:\n        {statement}"
        )
    return f"\ndef unused() -> None:\n    {statement}"


def _source(
    package: Path,
    *,
    count: int = 2,
    alias: str = "record",
    scope: str | None = "function",
    guarantee: bool = False,
) -> str:
    decorator = "@clauz3.guarantee(checks.unique_values())\n" if guarantee else ""
    return (
        "import clauz3\n"
        f"from {package.name} import contracts as checks\n"
        f"from {package.name}.effects import record as {alias}\n\n"
        f"{decorator}def main() -> None:\n"
        + f'    {alias}("bob")\n' * count
        + (_unused_import(scope, alias) if scope is not None else "")
    )


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


def _calls(source: str, package: Path) -> set[str]:
    return transitive_calls(
        source, "main", trusted_roots=[package], import_roots=[package.parent]
    )


@pytest.mark.parametrize(
    ("scope", "alias"),
    [
        ("function", "record"),
        ("class", "record"),
        ("nested-function", "record"),
        ("function", "emit"),
    ],
    ids=["unused-function", "unused-class", "nested-unused-function", "renamed-effect"],
)
def test_unused_import_cannot_suppress_a_required_contract(
    tmp_path: Path, scope: str, alias: str
) -> None:
    package = _layer(tmp_path)
    source = _source(package, scope=scope, alias=alias)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)
    assert _calls(source, package) == {"record"}


@pytest.mark.parametrize("count", [1, 2])
def test_unambiguous_required_policy_checks_the_actual_effect_count(
    tmp_path: Path, count: int
) -> None:
    package = _layer(tmp_path)
    source = _source(package, count=count, scope=None)

    proofs = _prove(source, package)

    expected = Conclusion.OK if count == 1 else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert "guarantee" in str(proofs[0].proof)


def test_unused_import_preserves_a_satisfied_required_contract(tmp_path: Path) -> None:
    package = _layer(tmp_path)
    source = _source(package, count=1)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert "guarantee" in str(proofs[0].proof)
    assert _calls(source, package) == {"record"}


@pytest.mark.parametrize("count", [1, 2])
def test_called_helper_resolves_its_own_local_effect_alias(
    tmp_path: Path, count: int
) -> None:
    package = _layer(tmp_path)
    source = (
        "import clauz3\n"
        f"from {package.name} import contracts as checks\n\n"
        "def deliver() -> None:\n"
        f"    from {package.name}.effects import record as emit\n"
        + '    emit("bob")\n' * count
        + "\ndef main() -> None:\n    deliver()\n"
        + _unused_import("function", "emit")
    )

    proofs = _prove(source, package)

    expected = Conclusion.OK if count == 1 else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert "guarantee" in str(proofs[0].proof)
    assert _calls(source, package) == {"deliver", "record"}


def test_required_violation_stops_run_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _layer(tmp_path)
    source = _source(package)
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
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()


def test_unused_import_does_not_change_the_actual_recorded_effects(
    tmp_path: Path,
) -> None:
    package = _layer(tmp_path)
    source = _source(package)

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )

    assert outcome.effects == (
        (f"{package.name}.effects.record", ("bob",), {}),
        (f"{package.name}.effects.record", ("bob",), {}),
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["bob", "bob"]


def test_unused_import_cannot_remove_an_effect_domain_from_approval(
    tmp_path: Path,
) -> None:
    package = _layer(tmp_path, required=False)
    source = _source(package)
    proofs = _prove(source, package)
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=proofs,
        trusted_roots=[package],
        import_roots=[tmp_path],
    )

    assert request["coverage"] == [
        {
            "domain": "recording",
            "status": "silent_gap",
            "missing_required": [],
            "missing_recommended": ["unique_values"],
        }
    ]


@pytest.mark.parametrize("scope", ["function", "class"])
def test_unused_module_alias_cannot_change_a_guarantees_domain(
    tmp_path: Path, scope: str
) -> None:
    package = _layer(tmp_path, required=False)
    source = _source(package, count=1, scope=None, guarantee=True)
    container = "def unused() -> None:" if scope == "function" else "class Unused:"
    source += f"\n{container}\n    import re as checks\n"
    proofs = _prove(source, package)
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=proofs,
        trusted_roots=[package],
        import_roots=[tmp_path],
    )

    assert request["guarantees"] == ["checks.unique_values()"]
    assert request["coverage"] == [
        {
            "domain": "recording",
            "status": "covered",
            "missing_required": [],
            "missing_recommended": [],
        }
    ]


def test_attribute_call_is_not_rewritten_through_an_unrelated_bare_alias(
    tmp_path: Path,
) -> None:
    package = _layer(tmp_path)
    source = (
        f"from {package.name} import effects\n"
        "from re import match as record\n\n"
        'def main() -> None:\n    effects.record("bob")\n'
        + _unused_import("function", "record")
    )
    policy = DomainPolicy(
        when_used=("record",), required=("unique_values",), label="recording"
    )

    assert _calls(source, package) == {"record"}
    assert compute_coverage(
        source=source,
        guarantees=[],
        policies=[policy],
        import_roots=[tmp_path],
        trusted_roots=[package],
    ) == [
        {
            "domain": "recording",
            "status": "required_gap",
            "missing_required": ["unique_values"],
            "missing_recommended": [],
        }
    ]
