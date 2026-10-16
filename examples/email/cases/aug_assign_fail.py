# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


# `msg += ...` on a str is a plain rebinding, `msg = msg + ...`, so the prover
# tracks the concatenation and refutes the length bound. It is the same
# program as aug_assign_rewritten_fail.py, spelled with `+=`; before #109 it
# skipped because no handler was registered for `astroid.AugAssign`.
@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    msg = "hi"
    msg += " a much longer body than the contract allows"
    send_email("bob@example.com", msg)
