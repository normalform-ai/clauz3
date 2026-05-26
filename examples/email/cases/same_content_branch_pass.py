# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.same_content("bob@example.com", "ann@example.com"))
def main(use_first_message: bool) -> None:
    if use_first_message:
        send_email("bob@example.com", "first")
        send_email("ann@example.com", "first")
    else:
        send_email("bob@example.com", "second")
        send_email("ann@example.com", "second")
