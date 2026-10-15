# ruff: noqa: F821
#
# Issue #123. A counter guards the effect. The quantified path evaluates the
# body once, so `n` is symbolically always 1, `n > 2` is dead, and `at_most(0)`
# used to prove -- while the interpreter, given five rows, sends three emails.
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email

import clauz3


@clauz3.guarantee(contracts.at_most(0))
def main() -> None:
    n = 0
    for row in db_query_users100("users"):
        n = n + 1
        if n > 2:
            send_email(row.email, "hi")
