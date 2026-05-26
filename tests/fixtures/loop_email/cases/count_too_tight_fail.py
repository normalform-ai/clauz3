# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query_users100, send_email
from qtrusted.rows import UserRow


@clauz3.guarantee(
    contracts.at_most(10)
)  # too tight; db_query_users100 can return up to 100
def main() -> None:
    for row in db_query_users100("users"):
        send_email(row.email, "hi")
