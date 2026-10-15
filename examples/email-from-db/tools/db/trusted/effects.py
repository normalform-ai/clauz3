from tools.db.trusted.schemas import UserRow

import clauz3


@clauz3.trusted
def send_email(addr: str, msg: str) -> None:
    """MOCK trusted email sender."""
    pass


@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 100)
def db_query(table: str, where: dict[str, object] | None = None) -> list[UserRow]:
    """Trusted DB query returning at most 100 UserRows.

    ``table`` is the table name. ``where`` is an optional filter dict;
    only rows where all key=value constraints hold are returned.
    """
    return []
