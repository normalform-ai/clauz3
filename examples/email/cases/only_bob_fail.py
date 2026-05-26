# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("ann@example.com", "hi")
