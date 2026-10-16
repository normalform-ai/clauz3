from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
BANK_ROOT = ROOT / "examples/bank"
TRUSTED_ROOT = BANK_ROOT / "tools/bank/trusted"


CASES = [
    ("max_spend_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("max_spend_fail.py", Conclusion.FAIL, "guarantee"),
    ("max_spend_branch_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    (
        "for_loop_sum_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, assertion, assertion, guarantee",
    ),
    ("for_loop_sum_fail.py", Conclusion.FAIL, "guarantee"),
    ("only_account_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("only_account_fail.py", Conclusion.FAIL, "guarantee"),
    ("negative_amount_fail.py", Conclusion.FAIL, "assertion"),
    (
        "pay_bill_capped_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee, guarantee",
    ),
    ("pay_bill_uncapped_fail.py", Conclusion.FAIL, "guarantee"),
    ("pay_bill_cap_too_tight_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "aug_assign_loop_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, assertion, guarantee",
    ),
    ("aug_assign_loop_fail.py", Conclusion.FAIL, "guarantee"),
]


@pytest.mark.parametrize(("case_name", "conclusion", "description"), CASES)
def test_bank_case(case_name: str, conclusion: Conclusion, description: str) -> None:
    case = ROOT / "examples/bank/cases" / case_name

    results = prove_path(
        case,
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[BANK_ROOT],
    )

    assert len(results) == 1
    assert results[0].proof.conclusion is conclusion
    assert results[0].proof.description == description
    assert results[0].proof.error is None


def test_every_case_is_pinned() -> None:
    on_disk = {
        case.name
        for case in (BANK_ROOT / "cases").glob("*.py")
        if not case.name.startswith("policy_")
    }
    pinned = {case_name for case_name, _, _ in CASES}
    assert len(pinned) == len(CASES), "duplicate case in conclusion table"
    assert on_disk == pinned, f"unmatched cases: {sorted(on_disk ^ pinned)}"
