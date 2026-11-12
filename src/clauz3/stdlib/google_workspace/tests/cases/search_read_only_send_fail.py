from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import search_messages, send_email

import clauz3


@clauz3.guarantee(email.read_only())
def main() -> None:
    for message in search_messages("from:alice@example.com", 1):
        send_email("bob@example.com", "fwd", message.body, dry_run=True)
