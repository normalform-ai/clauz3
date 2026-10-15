# ruff: noqa: F821
#
# Issue #123. Rebinding the loop variable detaches the body from the row the
# quantifier ranges over, so what the body does is no longer a function of
# `rows[i]` at all.
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email
from qtrusted.rows import UserRow

import clauz3


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    rows = db_query_users100("users")
    for row in rows:
        row = rows[0]
        send_email(row.email, "hi")
