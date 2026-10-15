# ruff: noqa: F821
# Issue #129: before the fix this reported ``proved!`` because the aliased
# decorator was silently ignored, and the approval request the human sees
# listed no guarantees at all.
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3 as c


@c.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("eve@example.com", "exfil")
