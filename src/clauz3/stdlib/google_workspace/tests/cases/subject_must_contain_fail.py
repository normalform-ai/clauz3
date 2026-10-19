# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


@clauz3.guarantee(email.subject_must_contain("[DRAFT]"))
@clauz3.guarantee(email.dry_run_only())
def main() -> None:
    send_email("alice@example.com", "hello", "hi", dry_run=True)
