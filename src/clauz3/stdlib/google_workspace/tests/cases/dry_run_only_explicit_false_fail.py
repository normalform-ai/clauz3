# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


# Explicit dry_run=False with dry_run_only() guarantee is correctly
# rejected. This is the "intentional real send under a dry-run-only
# campaign" failure mode — caught at prove time.
@clauz3.guarantee(email.dry_run_only())
def main() -> None:
    send_email("alice@example.com", "[DRAFT] hello", "hi", dry_run=False)
