# ruff: noqa: F821
from tools.db.trusted import contracts as db

import clauz3


@clauz3.guarantee(db.no_db_reads())
def main() -> None:
    pass
