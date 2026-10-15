"""Application-database effects for the same-name collision fixture."""

import deal

from dbt.rows import UserRow


@deal.has("email")
def send_email(addr: str, msg: str) -> None:
    """Trusted email sender."""
    pass


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str) -> list[UserRow]:
    """Trusted DB query returning up to 100 application UserRows."""
    return []
