# ruff: noqa: F821
# Issue #129: ``import clauz3 as c`` names the same decorator as ``import
# clauz3``, so the guarantee must be honoured rather than dropped.
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3 as c


@c.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "hi")
