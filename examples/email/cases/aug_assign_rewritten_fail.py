# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


# The desugared form of aug_assign_skip.py: spelled `msg = msg + ...`, the
# same concatenation is fully analyzable and the prover refutes the length
# bound. This is the negative control for the claim that the skip next door
# is an evaluator-coverage gap (#109), not an unanalyzable program.
@clauz3.guarantee(emails.content_length_at_most(5))
def main() -> None:
    msg = "hi"
    msg = msg + " a much longer body than the contract allows"
    send_email("bob@example.com", msg)
