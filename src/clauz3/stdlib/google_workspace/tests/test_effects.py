"""Runtime routing tests. Every subprocess invocation is mocked."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import deal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.google_workspace.gmail.trusted import effects  # noqa: E402


class EmailEffectsTests(unittest.TestCase):
    def test_draft_creates_without_sending_and_preserves_content(self) -> None:
        body = "--looks-like-a-flag\nquotes ' \" and $(not-a-command)"
        with (
            patch.dict(os.environ, {"CLAUZ3_EMAIL_BACKEND": "gog"}),
            patch("tools.google_workspace.gmail.trusted.effects.subprocess.run") as run,
        ):
            effects.create_draft("alice@example.com", "--subject", body)
        run.assert_called_once_with(
            [
                "gog",
                "gmail",
                "drafts",
                "create",
                "--no-input",
                "--to=alice@example.com",
                "--subject=--subject",
                "--body=" + body,
            ],
            check=True,
        )

    def test_default_backend_creates_a_draft(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("tools.google_workspace.gmail.trusted.effects.subprocess.run") as run,
        ):
            effects.create_draft("alice@example.com", "hello", "hi")
        self.assertEqual(
            run.call_args.args[0][:4], ["gog", "gmail", "drafts", "create"]
        )

    def test_unsupported_backend_never_falls_back_to_sending(self) -> None:
        for backend in ("pa", "unknown"):
            with (
                self.subTest(backend=backend),
                patch.dict(os.environ, {"CLAUZ3_EMAIL_BACKEND": backend}),
                patch(
                    "tools.google_workspace.gmail.trusted.effects.subprocess.run"
                ) as run,
                self.assertRaisesRegex(ValueError, "create_draft requires"),
            ):
                effects.create_draft("alice@example.com", "hello", "hi")
            run.assert_not_called()

    def test_draft_failure_propagates_without_retry_or_send(self) -> None:
        failure = subprocess.CalledProcessError(1, ["gog", "gmail", "drafts", "create"])
        with (
            patch.dict(os.environ, {"CLAUZ3_EMAIL_BACKEND": "gog"}),
            patch(
                "tools.google_workspace.gmail.trusted.effects.subprocess.run",
                side_effect=failure,
            ) as run,
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            effects.create_draft("alice@example.com", "hello", "hi")
        self.assertIs(raised.exception, failure)
        run.assert_called_once()

    def test_invalid_draft_arguments_do_not_execute(self) -> None:
        for to, subject in (("alice", "hello"), ("alice@example.com", "")):
            with (
                self.subTest(to=to, subject=subject),
                patch(
                    "tools.google_workspace.gmail.trusted.effects.subprocess.run"
                ) as run,
                self.assertRaises(deal.PreContractError),
            ):
                effects.create_draft(to, subject, "hi")
            run.assert_not_called()

    def test_send_preview_does_not_create_a_draft_or_send(self) -> None:
        with patch(
            "tools.google_workspace.gmail.trusted.effects.subprocess.run"
        ) as run:
            effects.send_email("alice@example.com", "hello", "hi")
            effects.send_email("alice@example.com", "hello", "hi", dry_run=True)
        run.assert_not_called()

    def test_send_preconditions_apply_to_previews_and_real_sends(self) -> None:
        for dry_run in (True, False):
            for to, subject in (("alice", "hello"), ("alice@example.com", "")):
                with (
                    self.subTest(to=to, subject=subject, dry_run=dry_run),
                    patch(
                        "tools.google_workspace.gmail.trusted.effects.subprocess.run"
                    ) as run,
                    self.assertRaises(deal.PreContractError),
                ):
                    effects.send_email(to, subject, "hi", dry_run=dry_run)
                run.assert_not_called()

    def test_sending_is_still_a_separate_command(self) -> None:
        with (
            patch.dict(os.environ, {"CLAUZ3_EMAIL_BACKEND": "gog"}),
            patch("tools.google_workspace.gmail.trusted.effects.subprocess.run") as run,
        ):
            effects.send_email("alice@example.com", "hello", "hi", dry_run=False)
        run.assert_called_once_with(
            [
                "gog",
                "gmail",
                "send",
                "--to",
                "alice@example.com",
                "--subject",
                "hello",
                "--body",
                "hi",
            ],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
