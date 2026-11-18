# ruff: noqa
import clauz3
from cz3tools.db.trusted import contracts
from cz3tools.db.trusted.effects import send_email
from cz3tools.db.trusted.schemas import UserRow


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    send_email("admin@example.com", "manual")  # literal, not from query
