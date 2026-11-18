# ruff: noqa: F821
from cz3tools.email.trusted import contracts as emails
from cz3tools.email.trusted.effects import send_email

import clauz3


def notify_bob() -> None:
    send_email("bob@example.com", "hi")


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    notify_bob()
