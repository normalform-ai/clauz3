# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


def notify(addr: str, msg: str) -> None:
    send_email(addr, msg)


@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    notify("bob@example.com", "first")
    notify("bob@example.com", "second")
