from pathlib import Path

import pytest

from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"


@pytest.mark.parametrize(
    ("case_name", "ok", "description"),
    [
        ("only_bob_pass.py", True, "assertion, guarantee"),
        ("only_bob_fail.py", False, "guarantee"),
        ("none_pass.py", True, "guarantee"),
        ("none_fail.py", False, "guarantee"),
        (
            "no_guarantees_pass.py",
            True,
            "assertion, assertion, assertion, guarantee",
        ),
        ("helper_pass.py", True, "assertion, guarantee"),
        ("helper_fail.py", False, "guarantee"),
        ("precondition_fail.py", False, "assertion"),
        ("dead_branch_pass.py", True, "assertion, guarantee"),
        ("unique_recipients_pass.py", True, "assertion, assertion, guarantee"),
        ("unique_recipients_fail.py", False, "guarantee"),
        ("unique_recipients_branch_pass.py", True, "assertion, assertion, guarantee"),
        ("nobody_emailed_twice_fail.py", False, "guarantee"),
        ("at_most_two_pass.py", True, "assertion, assertion, guarantee"),
        ("at_most_two_fail.py", False, "guarantee"),
        ("bob_at_most_two_pass.py", True, "assertion, assertion, assertion, guarantee"),
        ("bob_at_most_two_fail.py", False, "guarantee"),
        (
            "bob_at_most_two_branch_pass.py",
            True,
            "assertion, assertion, assertion, assertion, guarantee",
        ),
        ("content_length_pass.py", True, "assertion, assertion, guarantee"),
        ("content_length_fail.py", False, "guarantee"),
        ("content_length_branch_pass.py", True, "assertion, assertion, guarantee"),
        ("same_content_pass.py", True, "assertion, assertion, guarantee"),
        ("same_content_fail.py", False, "guarantee"),
        (
            "same_content_branch_pass.py",
            True,
            "assertion, assertion, assertion, assertion, guarantee",
        ),
    ],
)
def test_email_case(case_name: str, ok: bool, description: str) -> None:
    case = ROOT / "examples/email/cases" / case_name

    results = prove_path(
        case,
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[EMAIL_ROOT],
    )

    assert len(results) == 1
    assert results[0].ok is ok
    assert results[0].proof.description == description
