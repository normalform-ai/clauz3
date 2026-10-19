# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


@clauz3.guarantee(email.no_emails())
def main() -> None:
    send_email("alice@example.com", "[DRAFT] hi", "hi", dry_run=True)
