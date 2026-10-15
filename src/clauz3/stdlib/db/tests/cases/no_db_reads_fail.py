# ruff: noqa: F821
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import read_rows

import clauz3


class UserRow(clauz3.Row):
    email: str


@clauz3.guarantee(db.no_db_reads())
def main() -> None:
    read_rows(UserRow, "/repo/data", "users", {})
