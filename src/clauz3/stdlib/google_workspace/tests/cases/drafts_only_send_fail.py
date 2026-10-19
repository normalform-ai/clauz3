from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft, send_email

import clauz3


@clauz3.guarantee(email.drafts_only())
def main() -> None:
    create_draft("alice@example.com", "hello", "hi")
    send_email("alice@example.com", "hello", "hi", dry_run=False)
