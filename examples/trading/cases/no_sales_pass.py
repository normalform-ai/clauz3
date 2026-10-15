# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals


@clauz3.guarantee(trading.none())
def main() -> None:
    _rows = list_approvals("equities", where={"approved": True})  # read, don't trade
