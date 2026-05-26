# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.recipient_at_most("bob@example.com", 2))
def main(use_third: bool) -> None:
    send_email("bob@example.com", "first")
    send_email("bob@example.com", "second")
    if use_third:
        send_email("ann@example.com", "third")
    else:
        send_email("cal@example.com", "third")
