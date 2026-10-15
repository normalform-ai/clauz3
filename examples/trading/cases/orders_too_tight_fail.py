# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals, sell_shares
from tools.trading.trusted.schemas import ApprovalRow


@clauz3.guarantee(trading.stock_from(ApprovalRow, "stock"))
@clauz3.guarantee(trading.shares_from(ApprovalRow, "shares"))
@clauz3.guarantee(trading.orders_at_most(10))
def main() -> None:
    # The provenance clauses hold. The count does not: `list_approvals` is
    # capped at 50 rows, so the loop can place up to 50 orders. This is what
    # pins `orders_at_most` — without it, a vacuous count bound would still let
    # `approved_sale_pass.py` prove.
    for row in list_approvals("equities", where={"approved": True}):
        sell_shares(row.stock, row.shares)
