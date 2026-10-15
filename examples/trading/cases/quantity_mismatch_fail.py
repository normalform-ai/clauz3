# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals, sell_shares
from tools.trading.trusted.schemas import ApprovalRow


@clauz3.guarantee(trading.stock_from(ApprovalRow, "stock"))
@clauz3.guarantee(trading.shares_from(ApprovalRow, "shares"))
def main() -> None:
    # The stock is approved; the quantity is the agent's own. This is the case
    # a stock-only gate would wave through.
    for row in list_approvals("equities", where={"approved": True}):
        sell_shares(row.stock, 500)
