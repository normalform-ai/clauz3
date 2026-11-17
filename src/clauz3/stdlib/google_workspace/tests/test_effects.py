"""Runtime routing tests. Every subprocess invocation is mocked."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import deal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.google_workspace.gmail.trusted import effects  # noqa: E402
from tools.google_workspace.gmail.trusted.schemas import Message  # noqa: E402


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
            timeout=effects.DEFAULT_TIMEOUT_SECONDS,
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
                "--no-input",
                "--to=alice@example.com",
                "--subject=hello",
                "--body=hi",
            ],
            timeout=effects.DEFAULT_TIMEOUT_SECONDS,
            check=True,
        )

    def test_send_keeps_option_like_content_as_values(self) -> None:
        subject = "--cc=eve@example.com"
        body = "--attach=notes.txt\nquotes ' \" and $(not-a-command)"
        with (
            patch.dict(os.environ, {"CLAUZ3_EMAIL_BACKEND": "gog"}),
            patch("tools.google_workspace.gmail.trusted.effects.subprocess.run") as run,
        ):
            effects.send_email("-x@example.com", subject, body, dry_run=False)
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            [
                "gog",
                "gmail",
                "send",
                "--no-input",
                "--to=-x@example.com",
                "--subject=" + subject,
                "--body=" + body,
            ],
        )
        # No value stands alone, where gog could read it as an option.
        self.assertTrue(all(arg.startswith("--") for arg in argv[3:]))

    def test_every_command_has_a_timeout(self) -> None:
        self.assertEqual(effects.DEFAULT_TIMEOUT_SECONDS, 120)
        completed = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")

        def send() -> None:
            effects.send_email("a@example.com", "hello", "hi", dry_run=False)

        calls = (
            ("gog", lambda: effects.search_messages("is:unread", 1)),
            ("gog", lambda: effects.create_draft("a@example.com", "hello", "hi")),
            ("gog", send),
            ("pa", send),
        )
        for backend, call in calls:
            with (
                self.subTest(backend=backend),
                patch.dict(os.environ, {"CLAUZ3_EMAIL_BACKEND": backend}),
                patch(
                    "tools.google_workspace.gmail.trusted.effects.subprocess.run",
                    return_value=completed,
                ) as run,
            ):
                call()
            self.assertEqual(run.call_args.kwargs["timeout"], 120)

    def test_search_returns_message_rows_without_writing(self) -> None:
        stdout = json.dumps(
            [
                {
                    "id": "m1",
                    "threadId": "t1",
                    "date": "2026-11-06 15:59",
                    "from": "Alice <alice@example.com>",
                    "subject": "hello",
                    "labels": ["INBOX", "UNREAD"],
                    "body": "hi there",
                }
            ]
        )
        completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with patch(
            "tools.google_workspace.gmail.trusted.effects.subprocess.run",
            return_value=completed,
        ) as run:
            messages = effects.search_messages("-in:spam from:alice", 1)
        run.assert_called_once_with(
            [
                "gog",
                "gmail",
                "messages",
                "search",
                "--no-input",
                "--json",
                "--results-only",
                "--include-body",
                "--max=1",
                "--",
                "-in:spam from:alice",
            ],
            capture_output=True,
            text=True,
            timeout=effects.DEFAULT_TIMEOUT_SECONDS,
            check=True,
        )
        self.assertEqual(
            messages,
            [
                Message(
                    id="m1",
                    thread_id="t1",
                    date="2026-11-06 15:59",
                    sender="Alice <alice@example.com>",
                    sender_address="alice@example.com",
                    subject="hello",
                    labels="INBOX,UNREAD",
                    body="hi there",
                )
            ],
        )

    def test_search_with_no_matches_returns_empty_list(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="[]\n", stderr="")
        with patch(
            "tools.google_workspace.gmail.trusted.effects.subprocess.run",
            return_value=completed,
        ):
            self.assertEqual(effects.search_messages("from:nobody", 5), [])

    def test_search_with_null_result_returns_empty_list(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="null\n", stderr="")
        with patch(
            "tools.google_workspace.gmail.trusted.effects.subprocess.run",
            return_value=completed,
        ):
            self.assertEqual(effects.search_messages("from:nobody", 5), [])

    def test_search_with_unexpected_result_shape_fails(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout='{"id": "m1"}', stderr="")
        with (
            patch(
                "tools.google_workspace.gmail.trusted.effects.subprocess.run",
                return_value=completed,
            ),
            self.assertRaises(ValueError),
        ):
            effects.search_messages("from:alice", 5)

    def test_search_null_fields_become_empty_strings(self) -> None:
        stdout = json.dumps(
            [
                {
                    "id": "m1",
                    "threadId": "t1",
                    "date": None,
                    "from": None,
                    "subject": None,
                    "labels": None,
                    "body": None,
                }
            ]
        )
        completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with patch(
            "tools.google_workspace.gmail.trusted.effects.subprocess.run",
            return_value=completed,
        ):
            messages = effects.search_messages("from:alice", 1)
        self.assertEqual(
            messages,
            [
                Message(
                    id="m1",
                    thread_id="t1",
                    date="",
                    sender="",
                    sender_address="",
                    subject="",
                    labels="",
                    body="",
                )
            ],
        )

    def test_invalid_search_arguments_do_not_execute(self) -> None:
        for query, max_results in (("", 1), ("is:unread", 0), ("is:unread", 51)):
            with (
                self.subTest(query=query, max_results=max_results),
                patch(
                    "tools.google_workspace.gmail.trusted.effects.subprocess.run"
                ) as run,
                self.assertRaises(deal.PreContractError),
            ):
                effects.search_messages(query, max_results)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
