# ruff: noqa
import clauz3
from cz3tools.db.trusted import contracts
from cz3tools.db.trusted.effects import db_query, send_email
from cz3tools.db.trusted.schemas import UserRow


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users"):
        send_email(row.name, "hi")  # name, not email — wrong column!
