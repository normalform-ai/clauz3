"""Lambdas must observe their lexical capture in the currently executing branch."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import UnsupportedNode, execute
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, run_source


def _program(tmp_path: Path, case: str) -> tuple[str, Path]:
    package = tmp_path / f"lambda_captures_{uuid4().hex}"
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
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        '    return effect("record").empty()\n'
    )
    header = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent\n\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
    )
    initial = "    flag = 0\n    read = lambda: flag\n"
    if case.endswith("-mutation"):
        initial = "    values = []\n"
        mutations = {
            "captured-mutation": (
                "    mutate = lambda: values.append(1)\n    mutate()\n"
            ),
            "parameter-mutation": (
                "    mutate = lambda items: items.append(1)\n    mutate(values)\n"
            ),
            "wrapped-mutation": (
                "    mutate = lambda: values.append.__call__(1)\n    mutate()\n"
            ),
            "comprehension-mutation": (
                "    mutate = lambda: [values.append(1) for _ in [0]]\n    mutate()\n"
            ),
            "direct-mutation": "    values.append(1)\n",
        }
        body = mutations[case] + ('    if len(values) == 1:\n        record("one")\n')
    elif case == "unused-effect-default":
        initial = ""
        body = '    unused = lambda value=record("one"): 0\n'
    elif case in {
        "keyword-argument",
        "default-argument",
        "literal-default-argument",
        "positional-argument",
    }:
        initial = "    flag = 0\n"
        if case == "default-argument":
            body = (
                "    check = lambda flag=flag: flag == 0\n"
                "    flag = 1\n"
                "    if check():\n"
            )
        elif case == "literal-default-argument":
            body = "    check = lambda flag=1: flag == 1\n    if check():\n"
        else:
            argument = "flag=1" if case == "keyword-argument" else "1"
            body = f"    check = lambda flag: flag == 1\n    if check({argument}):\n"
        body += '        record("one")\n'
    elif case in {"within-then", "within-else", "untaken-branch"}:
        branch = {
            "within-then": "    if True:\n",
            "within-else": "    if False:\n        pass\n    else:\n",
            "untaken-branch": "    if False:\n",
        }[case]
        body = (
            branch + "        flag = 1\n"
            "        if read() == 1:\n"
            '            record("one")\n'
        )
        if case == "untaken-branch":
            body += "    assert read() == 0\n"
    elif case in {"after-merge", "after-untaken"}:
        condition = "True" if case == "after-merge" else "False"
        body = (
            f"    if {condition}:\n"
            "        flag = 1\n"
            "    if read() == 1:\n"
            '        record("one")\n'
        )
    elif case == "literal-loop":
        body = (
            "    for value in [1]:\n"
            "        flag = value\n"
            "        if read() == 1:\n"
            '            record("one")\n'
        )
    else:
        assert case == "stable"
        body = "    assert read() == 0\n"
    return header + initial + body, package


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


@pytest.mark.parametrize("case", ["within-then", "within-else", "untaken-branch"])
def test_lambda_capture_from_a_different_branch_is_refused(
    tmp_path: Path, case: str
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "lambda capture" in str(proofs[0].proof)
    assert "different symbolic scope" in str(proofs[0].proof)


@pytest.mark.parametrize(
    "case",
    [
        "keyword-argument",
        "default-argument",
        "literal-default-argument",
        "unused-effect-default",
    ],
)
def test_unsupported_lambda_argument_binding_cannot_suppress_an_effect(
    tmp_path: Path, case: str
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "unsupported lambda argument binding" in str(proofs[0].proof)


@pytest.mark.parametrize(
    "case",
    [
        "captured-mutation",
        "parameter-mutation",
        "wrapped-mutation",
        "comprehension-mutation",
    ],
)
def test_lambda_container_mutation_cannot_suppress_an_effect(
    tmp_path: Path, case: str
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    expected = {
        "captured-mutation": "mutating a captured collection is not supported",
        "parameter-mutation": (
            "mutating a function parameter or its alias is not supported"
        ),
        "wrapped-mutation": (
            "mutating method requires a direct call on a named receiver"
        ),
        "comprehension-mutation": "mutating a captured collection is not supported",
    }
    assert expected[case] in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("within-then", "lambda capture from a different symbolic scope"),
        ("within-else", "lambda capture from a different symbolic scope"),
        ("keyword-argument", "unsupported lambda argument binding"),
        ("default-argument", "unsupported lambda argument binding"),
        ("literal-default-argument", "unsupported lambda argument binding"),
        ("unused-effect-default", "unsupported lambda argument binding"),
        ("captured-mutation", "mutating a captured collection is not supported"),
        (
            "parameter-mutation",
            "mutating a function parameter or its alias is not supported",
        ),
        (
            "wrapped-mutation",
            "mutating method requires a direct call on a named receiver",
        ),
        (
            "comprehension-mutation",
            "mutating a captured collection is not supported",
        ),
    ],
)
def test_lambda_refusal_stops_run_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, diagnostic: str
) -> None:
    source, package = _program(tmp_path, case)
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


def test_explicit_positional_lambda_argument_refutes_the_guarantee(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, "positional-argument")

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("case", ["after-merge", "literal-loop", "direct-mutation"])
def test_established_lambda_mutation_controls_refute_the_guarantee(
    tmp_path: Path, case: str
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("case", ["after-untaken", "stable"])
def test_pure_lambda_control_remains_provable(tmp_path: Path, case: str) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("within-then", ["one"]),
        ("within-else", ["one"]),
        ("after-merge", ["one"]),
        ("literal-loop", ["one"]),
        ("untaken-branch", []),
        ("after-untaken", []),
        ("stable", []),
        ("keyword-argument", ["one"]),
        ("literal-default-argument", ["one"]),
        ("positional-argument", ["one"]),
        ("captured-mutation", ["one"]),
        ("parameter-mutation", ["one"]),
        ("comprehension-mutation", ["one"]),
        ("direct-mutation", ["one"]),
    ],
)
def test_runtime_reads_the_current_lambda_capture(
    tmp_path: Path, case: str, expected: list[str]
) -> None:
    source, package = _program(tmp_path, case)

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )

    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in expected
    )
    effects = importlib.import_module(f"{package.name}.effects")
    recorded = effects.EVENTS
    assert recorded == expected


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("default-argument", "default .* must be a constant expression"),
        ("unused-effect-default", "default .* must be a constant expression"),
        ("wrapped-mutation", "attribute access on BoundMethod"),
    ],
)
def test_interpreter_already_refuses_unsupported_lambda_forms(
    tmp_path: Path, case: str, diagnostic: str
) -> None:
    source, package = _program(tmp_path, case)

    with pytest.raises(UnsupportedNode, match=diagnostic):
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
