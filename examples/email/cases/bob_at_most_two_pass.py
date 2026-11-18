# ruff: noqa: F821
from cz3tools.email.trusted import contracts as emails
from cz3tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.recipient_at_most("bob@example.com", 2))
def main() -> None:
    send_email("bob@example.com", "first")
    send_email("ann@example.com", "hi")
    send_email("bob@example.com", "second")
