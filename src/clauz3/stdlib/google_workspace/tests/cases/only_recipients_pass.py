# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


@clauz3.guarantee(email.only_recipients(["alice@example.com", "bob@example.com"]))
@clauz3.guarantee(email.dry_run_only())
def main() -> None:
    send_email("alice@example.com", "[DRAFT] hi alice", "hi", dry_run=True)
    send_email("bob@example.com", "[DRAFT] hi bob", "hi", dry_run=True)
