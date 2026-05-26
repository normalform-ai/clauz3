# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import send_email
from qtrusted.rows import UserRow


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    send_email("admin@example.com", "manual")  # literal, not from query
