# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "hi")
    send_email("ann@example.com", "hi")
