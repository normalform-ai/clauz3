from pathlib import Path

from _pytest.capture import CaptureFixture

from clauz3.cli import main

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"
BANK_ROOT = ROOT / "examples/bank"
BANK_TRUSTED_ROOT = BANK_ROOT / "tools/bank/trusted"


def test_clauz3_prove_pass(capsys: CaptureFixture[str]) -> None:
    case = ROOT / "examples/email/cases/only_bob_pass.py"

    result = main(
        [
            "prove",
            str(case),
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 0
    assert "main: proved! assertion, guarantee" in capsys.readouterr().out


def test_clauz3_policy_check_decisions(capsys: CaptureFixture[str]) -> None:
    policy = EMAIL_ROOT / "approval-policy.json"
    expectations = {
        "cases/only_bob_pass.py": "auto_approved",
        "cases/policy_reject.py": "auto_rejected",
        "cases/policy_ask.py": "ask",
    }
    for case, expected in expectations.items():
        result = main(
            [
                "policy-check",
                str(EMAIL_ROOT / case),
                "--policy",
                str(policy),
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
                "--expect",
                expected,
            ]
        )
        assert result == 0, case
        assert expected in capsys.readouterr().out


def test_clauz3_policy_check_expect_mismatch_fails() -> None:
    result = main(
        [
            "policy-check",
            str(EMAIL_ROOT / "cases/policy_reject.py"),
            "--policy",
            str(EMAIL_ROOT / "approval-policy.json"),
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
            "--expect",
            "auto_approved",
        ]
    )
    assert result == 1


def test_clauz3_prove_fail(capsys: CaptureFixture[str]) -> None:
    case = ROOT / "examples/email/cases/only_bob_fail.py"

    result = main(
        [
            "prove",
            str(case),
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 1
    assert "main: failed guarantee" in capsys.readouterr().out


def test_clauz3_prove_with_multiple_trusted_roots(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    entry = tmp_path / "plan.py"
    entry.write_text(
        "\n".join(
            [
                "from tools.bank.trusted import contracts as bank",
                "from tools.bank.trusted.effects import withdraw",
                "from tools.email.trusted import contracts as emails",
                "from tools.email.trusted.effects import send_email",
                "",
                "import clauz3",
                "",
                '@clauz3.guarantee(emails.only(["bob@example.com"]))',
                "@clauz3.guarantee(bank.max_spend(5))",
                "def main() -> None:",
                '    send_email("bob@example.com", "hi")',
                '    withdraw("checking", 5)',
            ]
        )
    )

    result = main(
        [
            "prove",
            str(entry),
            "--trusted-roots",
            str(TRUSTED_ROOT),
            str(BANK_TRUSTED_ROOT),
            "--import-roots",
            str(EMAIL_ROOT),
            str(BANK_ROOT),
        ]
    )

    assert result == 0
    assert "main: proved!" in capsys.readouterr().out


def test_clauz3_rejects_untrusted_has(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    entry = tmp_path / "plan.py"
    entry.write_text(
        "\n".join(
            [
                "import deal",
                "",
                '@deal.has("trusted")',
                "def unsafe() -> None:",
                "    pass",
                "",
                "def main() -> None:",
                "    unsafe()",
            ]
        )
    )

    result = main(["prove", str(entry)])

    assert result == 2
    assert "@deal.has is only allowed in trusted roots" in capsys.readouterr().err


def test_clauz3_tools_discovers_email_tools(capsys: CaptureFixture[str]) -> None:
    result = main(["tools", "--import-root", str(EMAIL_ROOT)])

    assert result == 0
    out = capsys.readouterr().out
    assert "effect email/trusted/effects.py:send_email(addr, msg)" in out
    assert "contract email/trusted/contracts.py:only(addresses)" in out
