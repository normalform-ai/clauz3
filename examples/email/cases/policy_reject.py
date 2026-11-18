# ruff: noqa: F821
from cz3tools.email.trusted import contracts as emails
from cz3tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.no_guarantees())
def main() -> None:
    # Reaches a blocked recipient, so it cannot prove the deny rule's
    # avoidance obligation: the approval policy auto-rejects it.
    send_email("ceo@example.com", "please approve the acquisition")
