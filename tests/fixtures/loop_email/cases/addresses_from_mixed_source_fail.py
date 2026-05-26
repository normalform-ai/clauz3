# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query, send_email
from qtrusted.rows import UserRow


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    send_email("admin@example.com", "manual")  # literal — should fail
    for row in db_query("users"):
        send_email(row.email, "newsletter")
