# ruff: noqa: F821
# Issue #129: ``from clauz3 import guarantee as g`` is the other spelling the
# prover and the approval request used to drop.
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

from clauz3 import guarantee as g


@g(emails.none())
def main() -> None:
    send_email("bob@example.com", "hi")
