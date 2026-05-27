from pathlib import Path

import pytest

from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
BANK_ROOT = ROOT / "examples/bank"
TRUSTED_ROOT = BANK_ROOT / "tools/bank/trusted"


@pytest.mark.parametrize(
    ("case_name", "ok", "description"),
    [
        ("max_spend_pass.py", True, "assertion, assertion, guarantee"),
        ("max_spend_fail.py", False, "guarantee"),
        ("max_spend_branch_pass.py", True, "assertion, assertion, guarantee"),
        (
            "for_loop_sum_pass.py",
            True,
            "assertion, assertion, assertion, assertion, assertion, guarantee",
        ),
        ("for_loop_sum_fail.py", False, "guarantee"),
        ("only_account_pass.py", True, "assertion, assertion, guarantee"),
        ("only_account_fail.py", False, "guarantee"),
        ("negative_amount_fail.py", False, "assertion"),
        ("pay_bill_capped_pass.py", True, "assertion, assertion, guarantee, guarantee"),
        ("pay_bill_uncapped_fail.py", False, "guarantee"),
        ("pay_bill_cap_too_tight_fail.py", False, "guarantee"),
    ],
)
def test_bank_case(case_name: str, ok: bool, description: str) -> None:
    case = ROOT / "examples/bank/cases" / case_name

    results = prove_path(
        case,
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[BANK_ROOT],
    )

    assert len(results) == 1
    assert results[0].ok is ok
    assert results[0].proof.description == description
