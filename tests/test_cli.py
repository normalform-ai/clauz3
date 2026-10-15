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


def test_clauz3_prove_proves_each_entry_on_its_own(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """Issue #1: entry files are never concatenated into one module.

    ``prove`` accepts several entries but parses and proves each one on its
    own, so a ``main`` in one file cannot shadow or mask a ``main`` in
    another, whichever order they are given in.
    """
    passing = tmp_path / "passing.py"
    passing.write_text("def main() -> int:\n    return 1\n")
    failing = tmp_path / "failing.py"
    failing.write_text("def main() -> int:\n    assert False\n    return 1\n")

    for entries in ([passing, failing], [failing, passing]):
        assert main(["prove", *map(str, entries)]) == 1
        out = capsys.readouterr().out
        assert f"{passing}: main: proved!" in out
        assert f"{failing}: main: failed assertion" in out

    # A single entry keeps its unprefixed output.
    assert main(["prove", str(passing)]) == 0
    assert capsys.readouterr().out.startswith("main: proved!")
    assert main(["prove", str(failing)]) == 1
    assert "main: failed assertion" in capsys.readouterr().out


def test_clauz3_prove_reports_every_entry_and_the_worst_status(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """A configuration error in one entry does not stop the others."""
    broken = tmp_path / "broken.py"
    broken.write_text("import helpers\n\n\ndef main() -> None:\n    pass\n")
    failing = tmp_path / "failing.py"
    failing.write_text("def main() -> int:\n    assert False\n    return 1\n")
    passing = tmp_path / "passing.py"
    passing.write_text("def main() -> int:\n    return 1\n")

    assert main(["prove", str(broken), str(failing), str(passing)]) == 2
    captured = capsys.readouterr()
    assert f"{failing}: main: failed assertion" in captured.out
    assert f"{passing}: main: proved!" in captured.out
    assert captured.err.count(str(broken)) == 1
    assert "import of 'helpers' is not in the allowlist" in captured.err


def test_clauz3_prove_reports_unreadable_entries_and_continues(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """Missing, undecodable and unparseable entries don't abort the run."""
    passing = tmp_path / "passing.py"
    passing.write_text("def main() -> int:\n    return 1\n")
    missing = tmp_path / "missing.py"
    unparseable = tmp_path / "unparseable.py"
    unparseable.write_text("def main(:\n")
    undecodable = tmp_path / "undecodable.py"
    undecodable.write_bytes(b"\xff\xfe")

    for broken in (missing, unparseable, undecodable):
        for entries in ([broken, passing], [passing, broken]):
            assert main(["prove", *map(str, entries)]) == 2
            captured = capsys.readouterr()
            assert f"{passing}: main: proved!" in captured.out
            assert captured.err.startswith("error: ")
            assert str(broken) in captured.err
            assert "Traceback" not in captured.err


def test_clauz3_tools_discovers_email_tools(capsys: CaptureFixture[str]) -> None:
    result = main(["tools", "--import-root", str(EMAIL_ROOT)])

    assert result == 0
    out = capsys.readouterr().out
    assert "effect email/trusted/effects.py:send_email(addr, msg)" in out
    assert "contract email/trusted/contracts.py:only(addresses)" in out


def test_clauz3_tools_lists_by_resolution_not_by_spelling(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """The listing and the prover must agree on what a boundary is.

    Matching the decorator's source text omitted an aliased ``@c.trusted`` ---
    an effect the agent is then not told it may call --- and listed a bare
    ``@deal.has()``, which asserts the opposite of a boundary.
    """

    trusted = tmp_path / "tools/home/trusted"
    trusted.mkdir(parents=True)
    (trusted / "__init__.py").write_text("")
    (trusted / "effects.py").write_text(
        "import clauz3 as c\n"
        "import deal\n"
        "\n"
        "\n"
        "@c.trusted\n"
        "def lock_door(room: str) -> None:\n"
        "    pass\n"
        "\n"
        "\n"
        "@deal.has()\n"
        "def pure_helper(room: str) -> str:\n"
        "    return room\n"
    )

    assert main(["tools", "--import-root", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "effect home/trusted/effects.py:lock_door(room)" in out
    assert "pure_helper" not in out


def test_clauz3_approval_keygen_emits_usable_keypair(
    capsys: CaptureFixture[str],
) -> None:
    from clauz3.approvals.receipts import (
        SIGNING_KEY_ENV,
        VERIFY_KEY_ENV,
        consent_digest,
        sign_receipt,
        verify_receipt,
    )

    result = main(["approval-keygen"])
    assert result == 0

    out = capsys.readouterr().out
    keys: dict[str, str] = {}
    for line in out.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        keys[name] = value
    assert SIGNING_KEY_ENV in keys
    assert VERIFY_KEY_ENV in keys

    # The emitted pair must actually sign/verify a receipt.
    consent = consent_digest(program_sha256="a" * 64, guarantees=[], coverage=[])
    receipt = sign_receipt(
        signing_key_b64=keys[SIGNING_KEY_ENV],
        request_id="clr_x",
        program_sha256="a" * 64,
        decision="approved_once",
        expires_at=2_000_000.0,
        nonce="n",
        consent_sha256=consent,
    )
    nonce = verify_receipt(
        verify_key_b64=keys[VERIFY_KEY_ENV],
        receipt=receipt,
        request_id="clr_x",
        program_sha256="a" * 64,
        consent_sha256=consent,
        now=1_000_000.0,
    )
    assert nonce == "n"
