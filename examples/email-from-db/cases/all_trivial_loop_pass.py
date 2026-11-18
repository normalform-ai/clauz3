# ruff: noqa
import clauz3
from cz3tools.db.trusted import contracts
from cz3tools.db.trusted.effects import db_query, send_email


@clauz3.guarantee(contracts.at_most(100))
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")
