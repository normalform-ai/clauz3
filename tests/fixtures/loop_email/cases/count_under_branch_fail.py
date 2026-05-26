# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email


@clauz3.guarantee(contracts.at_most(1))
def main() -> None:
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "hi")
