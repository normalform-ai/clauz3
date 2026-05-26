# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


@clauz3.guarantee(emails.no_guarantees())
def main() -> None:
    # Not blocked, but not within the auto-pass allow-list either, so no rule
    # fires and a human decides.
    send_email("stranger@partner.com", "intro")
