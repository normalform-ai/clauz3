# ruff: noqa: F821
from cz3tools.email.trusted import contracts as emails
from cz3tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.bob_gets_x())
def main() -> None:
    send_email("bob@example.com", "y")
