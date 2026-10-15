# ruff: noqa
import clauz3
from dbt import contracts
from dbt.effects import send_email
from dbt.rows import UserRow
from ldapt.effects import ldap_query


@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
def main() -> None:
    for row in ldap_query("ou=people"):
        send_email(row.email, "Newsletter")
