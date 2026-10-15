# ruff: noqa: F821
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import read_rows

import clauz3


class UserRow(clauz3.Row):
    email: str
    consented: bool


@clauz3.guarantee(db.only_where({"consented": True}))
def main() -> None:
    read_rows(UserRow, "/repo/data", "users", {"consented": True})
