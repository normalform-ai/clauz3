import ast
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"


CASES = [
    ("alias_import_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("alias_import_fail.py", Conclusion.FAIL, "guarantee"),
    ("aliased_guarantee_name_fail.py", Conclusion.FAIL, "guarantee"),
    ("only_bob_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("only_bob_fail.py", Conclusion.FAIL, "guarantee"),
    ("none_pass.py", Conclusion.OK, "guarantee"),
    ("none_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "no_guarantees_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, guarantee",
    ),
    # Two assertions: the helper's own (empty) precondition and the
    # send_email precondition its body demands, now discharged at the caller.
    ("helper_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("helper_fail.py", Conclusion.FAIL, "guarantee"),
    ("precondition_fail.py", Conclusion.FAIL, "assertion"),
    ("dead_branch_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("unique_recipients_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("unique_recipients_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "unique_recipients_branch_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("nobody_emailed_twice_fail.py", Conclusion.FAIL, "guarantee"),
    ("at_most_two_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("at_most_two_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "bob_at_most_two_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, guarantee",
    ),
    ("bob_at_most_two_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "bob_at_most_two_branch_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, assertion, guarantee",
    ),
    ("content_length_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("content_length_fail.py", Conclusion.FAIL, "guarantee"),
    # gh-125 at the prove_path level: the where().all() one-liner.
    ("bob_gets_x_one_line_fail.py", Conclusion.FAIL, "guarantee"),
    ("content_length_branch_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("same_content_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("same_content_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "same_content_branch_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, assertion, guarantee",
    ),
    ("aug_assign_skip.py", Conclusion.SKIP, "unsupported program body"),
    ("aug_assign_rewritten_fail.py", Conclusion.FAIL, "guarantee"),
]


@pytest.mark.parametrize(("case_name", "conclusion", "description"), CASES)
def test_email_case(case_name: str, conclusion: Conclusion, description: str) -> None:
    case = ROOT / "examples/email/cases" / case_name

    results = prove_path(
        case,
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[EMAIL_ROOT],
    )

    assert len(results) == 1
    assert results[0].proof.conclusion is conclusion
    assert results[0].proof.description == description
    if conclusion is not Conclusion.SKIP:
        assert results[0].proof.error is None


def test_bob_gets_x_lambdas_share_one_line() -> None:
    """The gh-125 fixture only guards the same-line path while it stays on one line.

    Reads the file rather than importing the package, so the guard does not
    depend on an earlier test having left the trusted root in ``sys.modules``.
    """
    tree = ast.parse((TRUSTED_ROOT / "contracts.py").read_text())
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "bob_gets_x"
    )
    lambdas = [node for node in ast.walk(func) if isinstance(node, ast.Lambda)]
    assert len(lambdas) == 2
    assert len({node.lineno for node in lambdas}) == 1, [n.lineno for n in lambdas]


def test_every_case_is_pinned() -> None:
    on_disk = {
        case.name
        for case in (EMAIL_ROOT / "cases").glob("*.py")
        if not case.name.startswith("policy_")
    }
    pinned = {case_name for case_name, _, _ in CASES}
    assert len(pinned) == len(CASES), "duplicate case in conclusion table"
    assert on_disk == pinned, f"unmatched cases: {sorted(on_disk ^ pinned)}"
