"""Mapping predicates cannot be proved in the decorated function's scope."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import deal
import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

_LEXICAL_MISMATCH = "@clauz3.requires(lambda _: x > 0)"
_SHORT_FORMS = [
    pytest.param("@clauz3.requires(lambda _: True)", id="native-requires"),
    pytest.param("@clauz3.ensures(lambda _: True)", id="native-ensures"),
    pytest.param("@deal.pre(lambda _: True)", id="legacy-pre"),
    pytest.param("@deal.post(lambda _: True)", id="legacy-post"),
    pytest.param("@deal.ensure(lambda _: True)", id="legacy-ensure"),
    pytest.param("@require_alias(lambda _: True)", id="imported-native-alias"),
    pytest.param("@cz.ensures(lambda _: True)", id="native-module-alias"),
    pytest.param("@pre_alias(lambda _: True)", id="imported-deal-alias"),
    pytest.param("@deal.chain(deal.pre(lambda _: True))", id="legacy-chain"),
    pytest.param("@d.chain(cz.requires(lambda _: True))", id="aliased-mixed-chain"),
    pytest.param("@deal.ensure(validator=lambda _: True)", id="keyword-predicate"),
]


def _program(
    tmp_path: Path,
    condition: str,
    *,
    argument: int = 1,
    assertion: str | None = None,
) -> tuple[str, Path]:
    package = tmp_path / f"short_condition_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "import clauz3 as cz\n"
        "import deal\n"
        "import deal as d\n"
        "from clauz3 import requires as require_alias\n"
        "from deal import pre as pre_alias\n\n"
        "x = -1\n"
        "EVENTS = []\n\n"
        f"{condition}\n"
        "@clauz3.trusted\n"
        "def check(x: int) -> int:\n"
        "    EVENTS.append(x)\n"
        "    return x\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def limited() -> ContractSpec:\n"
        '    return effect("check").count() <= 1\n'
    )
    call = f"check({argument})"
    body = call if assertion is None else f"assert {call} {assertion}"
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import check\n"
        f"from {package.name}.contracts import limited\n\n"
        "@clauz3.guarantee(limited())\n"
        "def main() -> None:\n"
        f"    {body}\n"
    )
    return source, package


def test_short_form_global_cannot_resolve_as_the_function_argument(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, _LEXICAL_MISMATCH)
    results = prove_text(
        source,
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "short-form" in str(results[0].proof)


def test_short_form_runtime_uses_its_lexical_global_and_rejects_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python sees global x=-1; the old proof incorrectly substituted argument x=1."""
    _, package = _program(tmp_path, _LEXICAL_MISMATCH)
    monkeypatch.syspath_prepend(str(tmp_path))
    effects = importlib.import_module(f"{package.name}.effects")

    with pytest.raises(deal.PreContractError):
        effects.check(1)
    assert effects.EVENTS == []


@pytest.mark.parametrize("condition", _SHORT_FORMS)
def test_short_form_predicate_is_refused_for_every_supported_decorator_route(
    tmp_path: Path, condition: str
) -> None:
    source, package = _program(tmp_path, condition)
    results = prove_text(
        source,
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "short-form" in str(results[0].proof)


@pytest.mark.parametrize(
    "condition",
    [_LEXICAL_MISMATCH, "@clauz3.ensures(lambda _: True)"],
    ids=["precondition-mismatch", "mapping-postcondition"],
)
def test_short_form_refusal_stops_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    source, package = _program(tmp_path, condition)
    approve = Mock(side_effect=AssertionError("short-form proof reached approval"))
    execute = Mock(side_effect=AssertionError("short-form proof reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert "short-form" in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("condition", "assertion"),
    [
        ("@clauz3.requires(lambda x: x > 0)", None),
        ("@deal.pre(lambda x: x > 0)", None),
        ("@clauz3.ensures(lambda result: result > 0)", "> 0"),
        ("@deal.post(lambda result: result > 0)", "> 0"),
        ("@deal.ensure(lambda x, result: result == x)", "== 1"),
    ],
)
def test_explicit_parameter_conditions_still_prove(
    tmp_path: Path, condition: str, assertion: str | None
) -> None:
    source, package = _program(tmp_path, condition, assertion=assertion)
    results = prove_text(
        source,
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_explicit_precondition_still_refutes_a_violating_argument(
    tmp_path: Path,
) -> None:
    source, package = _program(
        tmp_path, "@clauz3.requires(lambda x: x > 0)", argument=-1
    )
    results = prove_text(
        source,
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="plan.py",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
