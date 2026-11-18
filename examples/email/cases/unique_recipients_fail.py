# ruff: noqa: F821
from cz3tools.email.trusted import contracts as emails
from cz3tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "first")
    send_email("bob@example.com", "second")
