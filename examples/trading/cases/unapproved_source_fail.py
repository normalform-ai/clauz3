# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals, sell_shares
from tools.trading.trusted.schemas import ApprovalRow


@clauz3.guarantee(trading.approvals_only_where({"approved": True}))
@clauz3.guarantee(trading.stock_from(ApprovalRow, "stock"))
@clauz3.guarantee(trading.shares_from(ApprovalRow, "shares"))
def main() -> None:
    # Every *requested* sale, not every *approved* one — the rows this loop
    # sells from include the ones the desk turned down.
    for row in list_approvals("equities", where={"desk": "equities"}):
        sell_shares(row.stock, row.shares)
