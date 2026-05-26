# ruff: noqa
import clauz3
from tools.db.trusted import contracts
from tools.db.trusted.effects import db_query


@clauz3.guarantee(contracts.none())
def main() -> None:
    _rows = db_query("users")  # query but don't email
