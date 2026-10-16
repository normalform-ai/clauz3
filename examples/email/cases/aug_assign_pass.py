# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


# `msg += "!"` is `msg = msg + "!"`: a str is immutable, so augmented
# assignment rebinds the name and the prover tracks the new value.
@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    msg = "hi"
    msg += "!"
    send_email("bob@example.com", msg)
