# ruff: noqa: F821
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import read_rows

import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


@clauz3.guarantee(db.reads_at_most(1))
def main() -> None:
    for row in read_rows(UserRow, "/repo/data", "users", {"consented": True}):
        _ = row.email
