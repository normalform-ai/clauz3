# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.no_guarantees())
def main() -> None:
    send_email("bob@example.com", "long unrestricted content")
    send_email("ann@example.com", "different content")
    send_email("bob@example.com", "duplicate recipient")
