import io
import sys
from collections.abc import Iterable
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import pytest
from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from clauz3 import interp
from clauz3.approvals.client import APPROVAL_SERVICE_ENV, MockApprovalServer
from clauz3.approvals.receipts import SIGNING_KEY_ENV, VERIFY_KEY_ENV, generate_keypair
from clauz3.cli import main

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"


@pytest.fixture(autouse=True)
def _approval_keys(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Provision an Ed25519 keypair so the mock service signs and the runner
    verifies; isolate the single-use nonce store under tmp."""
    signing, verify = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    monkeypatch.setenv(VERIFY_KEY_ENV, verify)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))


PASSING_SOURCE = """
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "Report is ready")
"""

FAILING_SOURCE = """
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("ann@example.com", "Report is ready")
"""


# A SKIP whose cause is permanent: direct recursion fails closed by design
# (see test_function_control_flow.py). The AugAssign gap this test used to sit
# beside is closed (#109); anchoring on recursion kept it alive through that.
SKIPPED_SOURCE = """
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


def helper(n: int) -> int:
    if n > 0:
        return helper(n - 1)
    return 0


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    helper(1)
    send_email("bob@example.com", "hi")
"""


def test_clauz3_run_from_stdin_approved(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 0
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["kind"] == "clauz3.run"
    assert request["guarantees"] == [
        "emails.only(['bob@example.com'])",
        "emails.unique_recipients()",
    ]
    assert request["coverage"] == [
        {
            "domain": "email",
            "status": "covered",
            "missing_required": [],
            "missing_recommended": [],
        }
    ]
    out = capsys.readouterr().out
    assert "main: proved!" in out
    # ``RunOutcome.effects`` carried the observed effect sequence and nothing
    # showed it to the operator.
    assert (
        "executed: main\n"
        "effects: 1\n"
        "  tools.email.trusted.effects.send_email('bob@example.com', "
        "'Report is ready')\n"
    ) in out


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (RuntimeError("smtp down"), "runtime error: smtp down"),
        (
            interp.ProgramError("smtp down", kind="OSError"),
            "runtime error: <stdin>: OSError: smtp down",
        ),
    ],
)
def test_clauz3_run_lists_effects_that_fired_before_a_failure(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    fault: Exception,
    message: str,
) -> None:
    """A run that fails mid-way still reports what already happened.

    The trusted call is recorded before it is dispatched, so an effect that
    raised -- a trusted layer's own exception propagates unchanged, a program
    fault becomes ``RunProgramError`` -- is listed even though no
    ``RunOutcome`` comes back.
    """
    from clauz3 import runner

    real_execute = interp.execute

    def raising(*args: object, **kwargs: object) -> None:
        raise fault

    def execute(*args: Any, **kwargs: Any) -> interp.Outcome:
        return real_execute(*args, trusted_dispatch=raising, **kwargs)

    monkeypatch.setattr(runner.interp, "execute", execute)  # type: ignore[attr-defined]

    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))
        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    captured = capsys.readouterr()
    assert result == 1
    assert "executed:" not in captured.out
    assert (
        "effects: 1\n"
        "  tools.email.trusted.effects.send_email('bob@example.com', "
        "'Report is ready')\n"
    ) in captured.out
    assert message in captured.err


def test_step_budget_below_one_is_a_usage_error_before_anything_runs(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """``--step-budget 0`` cannot run anything, so it fails at parse time.

    It used to prove, request approval, wait for the human, consume the
    single-use receipt nonce, and only then abort on the first ``frame.step()``.
    """
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))
        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "run",
                    "--step-budget",
                    "0",
                    "--trusted-root",
                    str(TRUSTED_ROOT),
                    "--import-root",
                    str(EMAIL_ROOT),
                ]
            )

    assert excinfo.value.code == 2
    assert server.requests == []
    assert "--step-budget: must be at least 1" in capsys.readouterr().err


def test_run_source_refuses_a_step_budget_below_one_before_approval() -> None:
    """The floor holds for library callers too, not only the CLI parser."""
    from clauz3.runner import RunConfigError, run_source

    with (
        MockApprovalServer(config={"decision": "approved_once"}) as server,
        pytest.raises(RunConfigError, match="at least 1"),
    ):
        run_source(
            PASSING_SOURCE,
            source_name="<stdin>",
            trusted_roots=[TRUSTED_ROOT],
            import_roots=[EMAIL_ROOT],
            approval_service_url=server.url,
            step_budget=0,
        )
    assert server.requests == []


def test_clauz3_run_discovers_trusted_roots_from_repo(
    monkeypatch: MonkeyPatch,
) -> None:
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))
        monkeypatch.chdir(EMAIL_ROOT)

        result = main(["run"])

    assert result == 0
    assert len(server.requests) == 1


def test_clauz3_run_requires_approval_service(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.delenv(APPROVAL_SERVICE_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "no approval service configured" in capsys.readouterr().err


def test_clauz3_run_rejected_by_approval_service(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    with MockApprovalServer(
        config={"decision": "request_more", "feedback": "prove nobody twice"},
    ) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 3
    err = capsys.readouterr().err
    assert "approval: request_more" in err
    assert "prove nobody twice" in err


def test_clauz3_run_does_not_submit_failed_proof(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(FAILING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 1
    assert server.requests == []
    assert "main: failed guarantee" in capsys.readouterr().out


def test_clauz3_run_does_not_submit_skipped_proof(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """A SKIP must be refused by ``run``, not just by ``prove``.

    ``run`` gates on ``conclusion != OK``, so leaving the provable subset has
    to be as fatal as a counterexample -- otherwise "nothing runs unproven"
    would hold only on the ``prove`` path. Nothing may reach the approval
    service either: asking a human to approve a program the prover could not
    analyse is the failure this gate exists to prevent.
    """
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(SKIPPED_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 1
    assert server.requests == []
    assert "main: skipped unsupported program body" in capsys.readouterr().out


def test_clauz3_run_refuses_while_skip_case_at_preflight(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """The shipped ``while`` skip case is refused by the *shipped* preflight.

    On the run path ``_preflight_structure`` runs before the prover, so this
    case exits 2 as a config refusal and never reaches the SKIP gate that
    ``test_clauz3_run_does_not_submit_skipped_proof`` pins. Feeding the real
    case file through ``main(["run", ...])`` pins the shipped gate, the CLI
    exit-code contract, and the no-approval-request property together --
    rather than a test-local model of the gate (see test_interp_corpus.py).
    """
    source = (EMAIL_ROOT / "cases" / "while_loop_skip.py").read_text()
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(source))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 2
    assert server.requests == []
    assert "While" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("case_name", "status", "requests", "message"),
    [
        ("aug_assign_pass.py", 0, 1, "main: proved!"),
        ("aug_assign_fail.py", 1, 0, "main: failed guarantee"),
    ],
)
def test_clauz3_run_proves_aug_assign_cases(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    case_name: str,
    status: int,
    requests: int,
    message: str,
) -> None:
    """``msg += ...`` passes the preflight and is decided by the prover (#109).

    The pass case is submitted for approval and runs; the fail case is
    refuted and never reaches the approval service.
    """
    source = (EMAIL_ROOT / "cases" / case_name).read_text()
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(source))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == status
    assert len(server.requests) == requests
    assert message in capsys.readouterr().out


def test_clauz3_run_rejects_direct_side_effect_import(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("import subprocess\n\ndef main() -> None:\n    pass\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "import of 'subprocess' is not in the allowlist" in capsys.readouterr().err


def test_clauz3_run_rejects_builtins_import(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("import builtins\n\ndef main() -> None:\n    pass\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "import of 'builtins' is not in the allowlist" in capsys.readouterr().err


def test_clauz3_run_rejects_reflective_builtin_access(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("def main() -> None:\n    getattr(object, '__class__')\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    # Refused, but by the proof gate rather than an AST denylist: the runtime
    # is no longer ``exec``, so ``FORBIDDEN_BUILTINS`` is gone. ``getattr`` is
    # not a name the prover can resolve, so the theorem does not come back OK
    # and ``run`` stops before approval. Even if it had proved,
    # ``clauz3.interp`` has no ``getattr`` to call -- see
    # ``tests/test_interp_escapes.py::test_getattr_indirection_is_not_available``.
    assert result == 1
    assert "skipped" in capsys.readouterr().out


def test_clauz3_run_rejects_unlisted_stdlib_import(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Modules not in the denylist used to slip through; allowlist closes that."""
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("import pickle\n\ndef main() -> None:\n    pass\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "import of 'pickle' is not in the allowlist" in capsys.readouterr().err


def test_clauz3_run_allows_extra_import_when_opted_in(
    monkeypatch: MonkeyPatch,
) -> None:
    """``--allow-import pickle`` lifts the gate for that module only."""

    source = (
        "import pickle  # noqa: F401\n"
        "import clauz3\n"
        "from tools.email.trusted import contracts as emails\n"
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "@clauz3.guarantee(emails.only(['bob@example.com']))\n"
        "@clauz3.guarantee(emails.unique_recipients())\n"
        "def main() -> None:\n"
        "    send_email('bob@example.com', 'Report is ready')\n"
    )
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(source))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
                "--allow-import",
                "pickle",
            ]
        )

    assert result == 0
    assert len(server.requests) == 1


def test_clauz3_run_reports_a_program_fault_as_exit_1(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """A proved, approved program that faults exits 1, not 2.

    Exit 2 is reserved for "this source should not have been run" -- a bad
    config, a disallowed import, a construct outside the subset. Collapsing a
    runtime fault into it would tell an agent to rewrite a program that was
    accepted and simply failed.
    """
    from clauz3 import interp, runner

    def boom(*args: object, **kwargs: object) -> None:
        raise interp.ProgramError("boom", kind="ValueError")

    monkeypatch.setattr(runner.interp, "execute", boom)  # type: ignore[attr-defined]

    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 1
    assert "runtime error: <stdin>: ValueError: boom" in capsys.readouterr().err


def test_import_disappearing_after_receipt_verification_is_exit_2(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    from clauz3 import runner
    from clauz3.interp import import_bindings

    verified = False
    original_consume = runner._consume_nonce
    original_find = import_bindings._find_spec

    def consume_then_hide(nonce: str) -> None:
        nonlocal verified
        original_consume(nonce)
        verified = True

    def disappear_after_verification(
        name: str, paths: Iterable[str]
    ) -> ModuleSpec | None:
        if verified and name == "statistics":
            return None
        return original_find(name, paths)

    monkeypatch.setattr(runner, "_consume_nonce", consume_then_hide)
    monkeypatch.setattr(import_bindings, "_find_spec", disappear_after_verification)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO("import statistics\n" + PASSING_SOURCE)
    )
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert verified
    assert len(server.requests) == 1
    assert result == 2
    output = capsys.readouterr()
    assert "cannot resolve import 'statistics'" in output.err
    assert "runtime error:" not in output.err
    assert "Report is ready" not in output.out


@pytest.mark.parametrize(
    ("label", "source", "match"),
    [
        (
            # Proves OK: the prover never evaluates module level. Refused by
            # `_exec_module`, which used to run only after approval.
            "module-level-non-constant",
            "import clauz3\n"
            "from tools.email.trusted import contracts as emails\n"
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "OTHER = 5\n"
            "LIMIT = OTHER\n"
            "\n"
            '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
            "def main() -> None:\n"
            '    send_email("bob@example.com", "hi")\n',
            "must be a constant",
        ),
        (
            # A construct with no handler in either registry. The prover skips
            # it, but the refusal should not wait for the run either.
            "outside-the-subset",
            "import clauz3\n"
            "from tools.email.trusted import contracts as emails\n"
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
            "def main() -> None:\n"
            "    x = (i for i in [1])\n"
            '    send_email("bob@example.com", "hi")\n',
            "not part of the proved subset",
        ),
    ],
)
def test_structural_refusal_happens_before_the_approval_round_trip(
    label: str,
    source: str,
    match: str,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """No approval request, so no human decision and no burned receipt nonce.

    Moving the structural rules into ``interp.execute`` moved them past
    ``prove_text``, past the approval request, past the human decision, and past
    ``_verify_receipt_or_refuse``, which consumes the single-use nonce. The
    deleted ``_validate_module_level`` ran before proving. A program that proves
    and is then refused would burn a receipt on a run that never happened.
    """
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(source))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 2
    assert server.requests == []
    assert match in capsys.readouterr().err
