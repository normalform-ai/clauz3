# ruff: noqa: F821
from qtrusted import contracts
from qtrusted.effects import db_query

import clauz3


@clauz3.guarantee(contracts.no_emails())
def main() -> None:
    _rows = db_query("users")  # query but don't email
