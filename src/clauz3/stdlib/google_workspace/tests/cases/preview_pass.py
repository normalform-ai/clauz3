from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


@clauz3.guarantee(email.dry_run_only())
@clauz3.guarantee(email.no_drafts())
def main() -> None:
    send_email("alice@example.com", "hello", "hi", dry_run=True)
