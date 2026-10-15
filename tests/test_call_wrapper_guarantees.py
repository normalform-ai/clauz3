"""Callable wrappers must be refused before a partially executed program."""

import importlib
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import UnsupportedNode, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

WRAPPER_BODIES = (
    "assert helper.__call__(value=7) == 7",
    "assert helper.__call__.__call__(value=7) == 7",
    "saved = helper.__call__\nassert saved(value=7) == 7",
    "callback = lambda value: value\nassert callback.__call__(7) == 7",
    "callback = lambda value: value\nassert callback.__call__.__call__(7) == 7",
    "callback = lambda value: value\nsaved = callback.__call__\nassert saved(7) == 7",
    "text = 'seven'\nmethod = text.startswith\nassert method.__call__('sev')",
    "saved = (1).__call__",
)
WRAPPER_IDS = (
    "helper",
    "repeated-helper",
    "saved-helper",
    "lambda",
    "repeated-lambda",
    "saved-lambda",
    "bound-method",
    "saved-scalar-attribute",
)
DIAGNOSTIC = (
    "explicit __call__ attributes are not supported; call the function directly"
)


def _program(tmp_path: Path, body: str, *, minimum: int = 2) -> tuple[str, Path]:
    package = tmp_path / f"call_wrappers_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def enough_records() -> ContractSpec:\n"
        f"    return effect('record').count() >= {minimum}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import enough_records\n"
        "def helper(value: int) -> int:\n"
        "    return value\n"
        "@clauz3.guarantee(enough_records())\n"
        "def main() -> None:\n"
        "    record('first')\n" + indent(body, "    ") + "\n    record('second')\n"
    )
    return source, package


@pytest.mark.parametrize("body", WRAPPER_BODIES, ids=WRAPPER_IDS)
def test_callable_wrapper_is_refused_before_approval_or_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    source, package = _program(tmp_path, body)

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert DIAGNOSTIC in str(proofs[0].proof)
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
    assert DIAGNOSTIC in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("body", "receiver"),
    [
        (WRAPPER_BODIES[0], "UserFunction"),
        (WRAPPER_BODIES[3], "LambdaFunction"),
        (WRAPPER_BODIES[6], "BoundMethod"),
    ],
    ids=["helper", "lambda", "bound-method"],
)
def test_bypassing_proof_would_abort_after_only_the_first_record(
    tmp_path: Path, body: str, receiver: str
) -> None:
    source, package = _program(tmp_path, body)

    with pytest.raises(
        UnsupportedNode,
        match=f"attribute access on {receiver} is not in the proved subset",
    ):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )

    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first"]


@pytest.mark.parametrize(
    "body",
    [
        "assert helper(value=7) == 7",
        "saved = helper\nassert saved(value=7) == 7",
        "callback = lambda value: value\nassert callback(7) == 7",
        "callback = lambda value: value\nsaved = callback\nassert saved(7) == 7",
        "text = 'seven'\nmethod = text.startswith\nassert method('sev')",
    ],
    ids=["helper", "helper-alias", "lambda", "lambda-alias", "method-alias"],
)
def test_supported_direct_calls_complete_the_guaranteed_records(
    tmp_path: Path, body: str
) -> None:
    source, package = _program(tmp_path, body)

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]
    assert outcome.effects == (
        (f"{package.name}.effects.record", ("first",), {}),
        (f"{package.name}.effects.record", ("second",), {}),
    )


def test_direct_call_control_cannot_satisfy_a_stronger_effect_guarantee(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, "assert helper(value=7) == 7", minimum=3)

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("expression", "receiver"),
    [
        ("'seven'.upper", "str"),
        ("(1).bit_length", "int"),
        ("[].sort", "list"),
        ("{}.items", "dict"),
        ("(1).__add__", "int"),
        ("'x'.__len__", "str"),
        ("(lambda value: value).__len__", "LambdaFunction"),
        ("(lambda value: value).imag", "LambdaFunction"),
    ],
    ids=[
        "upper",
        "bit-length",
        "sort",
        "items",
        "dunder-add",
        "dunder-len",
        "lambda-dunder-len",
        "lambda-imag",
    ],
)
def test_saving_an_unsupported_attribute_is_refused_before_the_first_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
    receiver: str,
) -> None:
    source, package = _program(tmp_path, f"saved = {expression}")

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    diagnostic = "attribute is outside the interpreter surface"
    assert diagnostic in str(proofs[0].proof)
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
    assert diagnostic in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    assert effects.EVENTS == []

    # Merely obtaining a bound attribute can abort the interpreter, even when
    # the saved value is never called. Proof must reject it before any effect.
    with pytest.raises(
        UnsupportedNode,
        match=f"attribute access on {receiver} is not in the proved subset",
    ):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    assert effects.EVENTS == ["first"]
