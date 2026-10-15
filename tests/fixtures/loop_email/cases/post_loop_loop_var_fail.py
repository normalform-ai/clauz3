# ruff: noqa: F821
#
# Issue #123. Python leaks the loop variable past the loop, bound to the last
# item; the prover cannot, because after the loop the quantifier is popped and
# `row` names an unconstrained index.
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email
from qtrusted.rows import UserRow

import clauz3


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query_users100("users"):
        send_email(row.email, "hi")
    send_email(row.email, "again")
