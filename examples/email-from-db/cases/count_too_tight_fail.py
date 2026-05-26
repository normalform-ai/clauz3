# ruff: noqa
import clauz3
from tools.db.trusted import contracts
from tools.db.trusted.effects import db_query, send_email


@clauz3.guarantee(
    contracts.count_at_most(10)
)  # too tight; db_query can return up to 100
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")
