# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


# `msg += ...` leaves the provable subset for a narrow reason: `_eval_stmt.py`
# registers no handler for `astroid.AugAssign`. It is not that the content is
# unanalyzable -- spelled `msg = msg + "..."` this same program proves FAIL,
# because both operands are string literals and the solver refutes the
# guarantee immediately. So the right long-term fix is a handler (see #109),
# not routing this category to runtime checks.
@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    msg = "hi"
    msg += " a much longer body than the contract allows"
    send_email("bob@example.com", msg)
