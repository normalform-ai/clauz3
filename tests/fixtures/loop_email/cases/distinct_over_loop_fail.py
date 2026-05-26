# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query, send_email
from qtrusted.rows import UserRow


@clauz3.guarantee(contracts.unique_recipients())
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")
