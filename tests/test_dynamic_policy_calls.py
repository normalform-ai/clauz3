"""Dynamic calls must retain required obligations and approval coverage."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import TrustedBoundaryError, execute
from clauz3.policy import compute_coverage, load_domain_policies, transitive_calls
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, build_approval_request, run_source

SHAPES = (
    "nested-callback",
    "returned-closure",
    "trusted-callback",
    "imported-invoke",
)


def _program(
    tmp_path: Path,
    shape: str,
    *,
    count: int = 2,
    required: bool = True,
    explicit: bool = False,
) -> tuple[str, Path]:
    package = tmp_path / f"dynamic_calls_{uuid4().hex}"
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
    decorator = "@clauz3.guarantee(checks.unique_values())\n" if explicit else ""
    if shape in {"nested-callback", "imported-invoke"}:
        if shape == "imported-invoke":
            (package / "helpers.py").write_text("def invoke(fn):\n    fn()\n")
            invoke = f"from {package.name}.helpers import invoke\n\n"
        else:
            invoke = "def invoke(fn) -> None:\n    fn()\n\n"
        body = (
            invoke + f"{decorator}def main() -> None:\n"
            "    def blast() -> None:\n"
            + '        record("bob")\n' * count
            + "    invoke(blast)\n"
        )
    elif shape == "returned-closure":
        body = (
            "def make():\n    def blast() -> None:\n"
            + '        record("bob")\n' * count
            + "    return blast\n\n"
            + f"{decorator}def main() -> None:\n    make()()\n"
        )
    elif shape == "trusted-callback":
        body = (
            "def invoke(fn) -> None:\n"
            + '    fn("bob")\n' * count
            + f"\n{decorator}def main() -> None:\n    invoke(record)\n"
        )
    elif shape == "dunder-call":
        body = (
            f"{decorator}def main() -> None:\n"
            "    def blast() -> None:\n"
            + '        record("bob")\n' * count
            + "    blast.__call__()\n"
        )
    elif shape == "imported-trusted-callback":
        (package / "helpers.py").write_text(
            "def invoke(fn):\n" + '    fn("bob")\n' * count
        )
        body = (
            f"from {package.name}.helpers import invoke\n\n"
            f"{decorator}def main() -> None:\n    invoke(record)\n"
        )
    else:
        assert shape == "subscript"
        body = (
            f"{decorator}def main() -> None:\n"
            '    handlers = ["not callable"]\n' + '    handlers[0]("bob")\n' * count
        )
    source = (
        "import clauz3\n"
        f"from {package.name} import contracts as checks\n"
        f"from {package.name}.effects import record\n\n" + body
    )
    return source, package


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("count", [1, 2])
def test_dynamic_effects_still_enforce_required_contracts(
    tmp_path: Path, shape: str, count: int
) -> None:
    source, package = _program(tmp_path, shape, count=count)

    proofs = _prove(source, package)

    expected = Conclusion.OK if count == 1 else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("count", [1, 2])
def test_imported_helper_calling_a_trusted_argument_keeps_required_contracts(
    tmp_path: Path, count: int
) -> None:
    source, package = _program(tmp_path, "imported-trusted-callback", count=count)

    proofs = _prove(source, package)

    expected = Conclusion.OK if count == 1 else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("shape", SHAPES)
def test_an_explicit_guarantee_already_refutes_the_dynamic_violation(
    tmp_path: Path, shape: str
) -> None:
    # This control proves the symbolic execution already sees both effects;
    # omitted required obligations, rather than lost effects, caused false OK.
    source, package = _program(tmp_path, shape, required=False, explicit=True)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("shape", SHAPES)
def test_dynamic_required_violation_stops_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    source, package = _program(tmp_path, shape)
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


@pytest.mark.parametrize("shape", SHAPES)
def test_approval_coverage_includes_dynamically_called_effects(
    tmp_path: Path, shape: str
) -> None:
    source, package = _program(tmp_path, shape, required=False)
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


@pytest.mark.parametrize("required", [False, True])
def test_unsupported_callable_wrapper_keeps_source_policy_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required: bool
) -> None:
    source, package = _program(tmp_path, "dunder-call", required=required)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "explicit __call__ attributes are not supported" in str(
        proofs[0].proof.error
    )
    # Coverage remains conservative for source it can inspect, independently
    # of the executable subset refusing this callable's attribute surface.
    assert "record" in transitive_calls(source, "main")
    policies = load_domain_policies(trusted_roots=[package], import_roots=[tmp_path])
    assert compute_coverage(source=source, guarantees=[], policies=policies) == [
        {
            "domain": "recording",
            "status": "required_gap" if required else "silent_gap",
            "missing_required": ["unique_values"] if required else [],
            "missing_recommended": [] if required else ["unique_values"],
        }
    ]
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []
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
    assert "explicit __call__ attributes are not supported" in str(
        exc_info.value.proofs[0].proof.error
    )
    approve.assert_not_called()
    execute_source.assert_not_called()
    assert effects.EVENTS == []


@pytest.mark.parametrize("shape", ["nested-callback", "returned-closure"])
def test_escaping_closures_really_execute_the_recorded_effects(
    tmp_path: Path, shape: str
) -> None:
    source, package = _program(tmp_path, shape)

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


def test_imported_helper_cannot_receive_an_interpreter_callback(tmp_path: Path) -> None:
    source, package = _program(tmp_path, "imported-invoke")

    with pytest.raises(TrustedBoundaryError, match="cannot pass UserFunction"):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(
                trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
            ),
            import_roots=[tmp_path],
        )

    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


def test_invalid_subscripted_callee_is_refused(tmp_path: Path) -> None:
    source, package = _program(tmp_path, "subscript")

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "'str' object is not callable" in str(proofs[0].proof)
