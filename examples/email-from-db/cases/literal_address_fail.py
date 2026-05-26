# ruff: noqa
import clauz3
from tools.db.trusted import contracts
from tools.db.trusted.effects import send_email
from tools.db.trusted.schemas import UserRow


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    send_email("admin@example.com", "manual")  # literal, not from query
