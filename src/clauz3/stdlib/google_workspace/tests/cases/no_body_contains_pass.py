# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


@clauz3.guarantee(email.no_body_contains("BEGIN PRIVATE KEY"))
@clauz3.guarantee(email.dry_run_only())
def main() -> None:
    send_email(
        "alice@example.com",
        "[DRAFT] notes",
        "no secrets here",
        dry_run=True,
    )
