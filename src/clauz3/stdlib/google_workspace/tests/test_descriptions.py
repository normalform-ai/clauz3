"""Approval descriptions use trusted templates and literal argument values."""

import sys
import tempfile
import unittest
from pathlib import Path

from clauz3.contract_metadata import describe_call
from clauz3.libraries.install import install_layer
from clauz3.sessions.catalogue import snapshot_catalogue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.google_workspace.gmail.trusted import contracts  # noqa: E402


class EmailDescriptionsTests(unittest.TestCase):
    def test_installed_tools_and_generated_skill_include_documentation(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            install_layer(source, into=project, generate_skills=True)
            trusted = project / ".clauz3/tools/google_workspace/gmail/trusted"
            api = "\n".join(snapshot_catalogue(project, [trusted])["api"])
            skill = (
                project / ".clauz3/skills/google_workspace/gmail/SKILL.md"
            ).read_text()
        self.assertIn("Effect: create_draft(", api)
        self.assertIn("Effect: send_email(", api)
        self.assertIn("*, dry_run: bool=True", api)
        self.assertIn("clauz3.requires(", api)
        self.assertIn("This creates a new message", api)
        self.assertIn("does not span separate runs", api)
        for summary in (
            "Save an unsent email in Gmail's Drafts folder",
            "Send immediately when ``dry_run=False``",
            "Cap the combined number of send_email and create_draft calls per run",
        ):
            self.assertIn(summary, api)
            self.assertIn(summary, skill)

    def test_every_email_contract_has_a_renderable_description(self) -> None:
        arguments: dict[str, list[object]] = {
            "no_guarantees": [],
            "dry_run_only": [],
            "no_emails": [],
            "only_recipients": [["alice@example.com"]],
            "unique_recipients": [],
            "emails_at_most": [1],
            "subject_must_contain": ["hello"],
            "subject_length_at_most": [20],
            "body_length_at_most": [200],
            "no_body_contains": ["secret"],
            "no_subject_contains": ["secret"],
            "drafts_only": [],
            "no_drafts": [],
        }
        for name, args in arguments.items():
            with self.subTest(name=name):
                self.assertIsNotNone(
                    describe_call(getattr(contracts, name), args=args, kwargs={})
                )

    def test_recipient_values_are_quoted_without_template_substitution(self) -> None:
        result = describe_call(
            contracts.only_recipients,
            args=[["{allowlist}@example.com"]],
            kwargs={},
        )
        assert result is not None
        self.assertIn('["{allowlist}@example.com"]', str(result["text"]))

    def test_drafts_and_previews_have_distinct_scope(self) -> None:
        draft = describe_call(contracts.drafts_only, args=[], kwargs={})
        preview = describe_call(contracts.dry_run_only, args=[], kwargs={})
        assert draft is not None and preview is not None
        self.assertIn("sending messages", str(draft["text"]))
        self.assertIn("forbidden", str(draft["text"]))
        self.assertIn("saving drafts is still allowed", str(preview["text"]))
