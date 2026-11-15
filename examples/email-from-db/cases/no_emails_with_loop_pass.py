# ruff: noqa
import clauz3
from cz3tools.db.trusted import contracts
from cz3tools.db.trusted.effects import db_query


@clauz3.guarantee(contracts.none())
def main() -> None:
    _rows = db_query("users")  # query but don't email
