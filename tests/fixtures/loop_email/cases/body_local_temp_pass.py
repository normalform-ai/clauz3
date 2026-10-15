# ruff: noqa: F821
#
# Issue #123. A per-row temporary read only inside the body is a function of
# the current row, which is exactly what the quantified model is faithful to.
# Refusing this too would cost the quantified path most of its usefulness, so
# the check is about *carried* state, not about assignment.
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email
from qtrusted.rows import UserRow

import clauz3


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query_users100("users"):
        addr = row.email
        send_email(addr, "hi")
