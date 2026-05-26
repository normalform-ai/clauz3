# ruff: noqa: F821
from qtrusted import contracts
from qtrusted.effects import db_query, send_email

import clauz3


@clauz3.guarantee(contracts.all_trivial())
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")
