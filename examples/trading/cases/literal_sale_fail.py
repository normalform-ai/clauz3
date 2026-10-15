# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import sell_shares
from tools.trading.trusted.schemas import ApprovalRow


@clauz3.guarantee(trading.stock_from(ApprovalRow, "stock"))
def main() -> None:
    sell_shares("ACME", 100)  # literal — no approval row backs this
