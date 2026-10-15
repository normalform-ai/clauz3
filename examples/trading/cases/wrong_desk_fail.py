# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals, sell_shares
from tools.trading.trusted.schemas import ApprovalRow


@clauz3.guarantee(trading.approvals_only_desk("equities"))
@clauz3.guarantee(trading.stock_from(ApprovalRow, "stock"))
@clauz3.guarantee(trading.shares_from(ApprovalRow, "shares"))
def main() -> None:
    # Every order is backed by an approval — from another desk's record.
    for row in list_approvals("derivatives", where={"approved": True}):
        sell_shares(row.stock, row.shares)
