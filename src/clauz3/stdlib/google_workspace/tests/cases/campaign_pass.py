# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


# A realistic agent-driven send campaign with full safety stack:
# - dry_run_only() — never actually fires
# - only_recipients allowlist — small explicit set
# - unique_recipients — no double-sends
# - emails_at_most cap — bounded volume
# - subject_must_contain "[DRAFT]" — visible machine-authored marker
# - no_body_contains "BEGIN PRIVATE KEY" — secrets exfil guard
@clauz3.guarantee(email.dry_run_only())
@clauz3.guarantee(email.only_recipients(["alice@example.com", "bob@example.com"]))
@clauz3.guarantee(email.unique_recipients())
@clauz3.guarantee(email.emails_at_most(2))
@clauz3.guarantee(email.subject_must_contain("[DRAFT]"))
@clauz3.guarantee(email.no_body_contains("BEGIN PRIVATE KEY"))
def main() -> None:
    send_email(
        "alice@example.com",
        "[DRAFT] status update",
        "all clear",
        dry_run=True,
    )
    send_email(
        "bob@example.com",
        "[DRAFT] status update",
        "all clear",
        dry_run=True,
    )
