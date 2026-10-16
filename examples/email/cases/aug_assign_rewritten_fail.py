# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


# The desugared form of aug_assign_fail.py: spelled `msg = msg + ...`, the
# same concatenation is refuted the same way. The pair pins that the `+=`
# handler (#109) proves exactly what its desugaring proves.
@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    msg = "hi"
    msg = msg + " a much longer body than the contract allows"
    send_email("bob@example.com", msg)
