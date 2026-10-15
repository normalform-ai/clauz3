# ruff: noqa: F821
#
# Issue #123. After the loop `target` is unconditionally `rows[i].email`, so
# `addresses_from` used to prove -- but the zero-row path is never considered,
# and with an empty result set the literal is what gets emailed.
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email
from qtrusted.rows import UserRow

import clauz3


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    target = "attacker@example.com"
    for row in db_query_users100("users"):
        target = row.email
    send_email(target, "hi")
