from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


@clauz3.guarantee(email.dry_run_only())
def main() -> None:
    send_email("alice@example.com", "", "hi", dry_run=True)
