# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query, send_email_only_to_bob


@clauz3.guarantee(contracts.all_trivial())
def main() -> None:
    for row in db_query("users"):
        send_email_only_to_bob(row.email, "hi")
