# ruff: noqa: F821
#
# Issue #123. `addr` is bound only inside the body, so the assignment is not
# loop-carried -- but the read after the loop has no value the prover can stand
# behind: the quantifier is gone, and on the zero-row path the name was never
# bound at all (the interpreter raises NameError there).
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email
from qtrusted.rows import UserRow

import clauz3


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query_users100("users"):
        addr = row.email
    send_email(addr, "hi")
