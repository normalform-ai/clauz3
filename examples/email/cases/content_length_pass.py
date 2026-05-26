# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    send_email("bob@example.com", "hello")
    send_email("ann@example.com", "hi")
