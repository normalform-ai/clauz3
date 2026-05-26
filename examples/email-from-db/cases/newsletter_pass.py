# ruff: noqa
import clauz3
from tools.db.trusted import contracts
from tools.db.trusted.effects import db_query, send_email
from tools.db.trusted.schemas import UserRow


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
@clauz3.guarantee(contracts.count_at_most(100))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "Newsletter is out!")
