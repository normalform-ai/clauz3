"""Issue #100: ``clauz3 prove`` must refuse what ``clauz3 run`` refuses at module scope.

The proof covers the target body. A bare call, an assignment whose value is a
trusted effect, or an effectful default argument all execute *outside* it, so a
program carrying one was certified ``proved!`` with exit 0 while ``clauz3 run``
refused the same file in ``runner._preflight_structure``. ``prove`` is
documented as a standalone gate and its exit code is what an approval harness
reads, so the two must not disagree.

The gate is the interpreter's own module-level pass -- ``interp._exec_module``
and the ``_require_inert`` family -- called from ``prover.validate_module_level``
at the untrusted-source boundary (``prove_path`` for ``clauz3 prove``,
``approval_policy._entails_all`` for ``clauz3 policy-check`` and the approval
service's auto-decisions), so every shipped prover entry point reads the one
rule ``run`` already enforces rather than a second statement of it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from _pytest.capture import CaptureFixture

from clauz3 import interp, runner
from clauz3._vendor.deal_solver import Conclusion
from clauz3.cli import main
from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"

_HEADER = (
    "import clauz3\n"
    "from tools.email.trusted import contracts as emails\n"
    "from tools.email.trusted.effects import send_email\n"
)
_CLEAN_MAIN = (
    '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
    "def main() -> None:\n"
    '    send_email("bob@example.com", "hi")\n'
)


def _prove(tmp_path: Path, source: str, *, extra_args: tuple[str, ...] = ()) -> int:
    program = tmp_path / "plan.py"
    program.write_text(source)
    return main(
        [
            "prove",
            str(program),
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
            *extra_args,
        ]
    )


@pytest.mark.parametrize(
    ("label", "source", "match"),
    [
        (
            "bare-call",
            _HEADER + _CLEAN_MAIN + "\nmain()\n",
            "module-level Expr would run outside the proved function",
        ),
        (
            "effect-assignment",
            _HEADER + 'SENT = send_email("eve@example.com", "exfil")\n' + _CLEAN_MAIN,
            "module-level assignment value must be a constant expression",
        ),
        (
            "effectful-default",
            _HEADER
            + '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
            + 'def main(x: int = send_email("eve@example.com", "exfil")) -> None:\n'
            + '    send_email("bob@example.com", "hi")\n',
            "default for 'x' must be a constant expression",
        ),
    ],
)
def test_prove_refuses_module_level_execution(
    label: str,
    source: str,
    match: str,
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """Exit 2 -- "this source should not have been submitted" -- not ``proved!``.

    Each of these proved OK on ``main`` at d87247d because the prover walks
    ``main``'s body only, while ``clauz3 run`` refused all three before the
    approval round trip. The message is the interpreter's, unchanged, so a
    harness sees the same reason from either command.
    """
    result = _prove(tmp_path, source)

    captured = capsys.readouterr()
    assert result == 2
    assert "proved!" not in captured.out
    assert match in captured.err


def test_inert_declarations_still_prove() -> None:
    """The gate admits exactly what ``run`` admits: declarations and constants."""
    results = prove_path(
        EMAIL_ROOT / "cases/only_bob_pass.py",
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[EMAIL_ROOT],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_constant_assignment_and_row_schema_still_prove(tmp_path: Path) -> None:
    program = tmp_path / "plan.py"
    program.write_text(
        _HEADER
        + "\n"
        + "MAX: int = 5\n"
        + "OFFSET = -3\n"
        + "FIRST, SECOND = (1, 2)\n"
        + "HEAD, *TAIL = [1, 2, 3]\n"
        + "pass\n"
        + "\n"
        + "class UserRow(clauz3.Row):\n"
        + "    name: str\n"
        + "\n"
        + _CLEAN_MAIN
    )
    results = prove_path(
        program,
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[EMAIL_ROOT],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_literal_positional_defaults_still_prove(tmp_path: Path) -> None:
    program = tmp_path / "plan.py"
    program.write_text(
        _HEADER
        + '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
        + 'def main(message: str = "hello", offset: int = -3) -> None:\n'
        + '    send_email("bob@example.com", message)\n'
    )

    results = prove_path(
        program, trusted_roots=[TRUSTED_ROOT], import_roots=[EMAIL_ROOT]
    )

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_keyword_only_defaults_keep_the_interpreter_refusal(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = "def main(*, offset: int = -3) -> None:\n    assert True\n"

    result = _prove(tmp_path, source)

    captured = capsys.readouterr()
    assert result == 2
    assert "keyword-only defaults are not in the proved subset" in captured.err


def test_allowlisted_from_import_still_requires_an_interpreter_surface(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = (
        "from typing import Final\n"
        "LIMIT: Final[int] = 1\n"
        "def main() -> None:\n"
        "    assert True\n"
    )

    result = _prove(tmp_path, source)

    captured = capsys.readouterr()
    assert result == 2
    assert "typing exposes nothing to interpreted programs" in captured.err
    assert "proved!" not in captured.out


@pytest.mark.parametrize(
    ("imports", "extra_args", "reason"),
    [
        (
            "from tools.email.trusted.missing_module_gate import send_email\n",
            (),
            "trusted module 'tools.email.trusted.missing_module_gate' is not "
            "loaded from a configured trusted root",
        ),
        (
            "import missing_module_gate_optin\n",
            ("--allow-import", "missing_module_gate_optin"),
            "No module named 'missing_module_gate_optin'",
        ),
    ],
    ids=["trusted-module", "explicitly-allowlisted-module"],
)
def test_unresolvable_allowed_module_is_a_clean_configuration_refusal(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    imports: str,
    extra_args: tuple[str, ...],
    reason: str,
) -> None:
    source = imports + "def main() -> None:\n    assert True\n"

    result = _prove(tmp_path, source, extra_args=extra_args)

    captured = capsys.readouterr()
    assert result == 2
    assert reason in captured.err
    assert "Traceback" not in captured.err
    assert "proved!" not in captured.out


def test_run_missing_allowed_import_is_refused_before_approval_and_execution(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = tmp_path / "plan.py"
    program.write_text(
        "import missing_module_gate_optin\ndef main() -> None:\n    assert True\n"
    )
    approve = Mock(side_effect=AssertionError("approval must not be requested"))
    execute = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr(runner, "submit_approval_request", approve)
    monkeypatch.setattr(interp, "execute", execute)

    result = main(
        [
            "run",
            str(program),
            "--import-root",
            str(tmp_path),
            "--allow-import",
            "missing_module_gate_optin",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "No module named 'missing_module_gate_optin'" in captured.err
    assert str(program) in captured.err
    assert "runtime error" not in captured.err
    assert "Traceback" not in captured.err
    assert "proved!" not in captured.out
    approve.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize(
    "target", ["selected", None], ids=["custom-target", "all-targets"]
)
def test_module_gate_does_not_require_a_function_named_main(
    tmp_path: Path, target: str | None
) -> None:
    program = tmp_path / "plan.py"
    program.write_text(
        "def selected() -> None:\n"
        "    assert True\n"
        "def other() -> None:\n"
        "    assert True\n"
    )

    results = prove_path(program, target=target)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK] * (
        1 if target is not None else 2
    )
