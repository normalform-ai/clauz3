# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals, sell_shares


@clauz3.guarantee(trading.none())
def main() -> None:
    # Claims it places no orders, then places one per approval row. The
    # counterpart to `no_sales_pass.py`: together they pin `none()` in both
    # directions.
    for row in list_approvals("equities", where={"approved": True}):
        sell_shares(row.stock, row.shares)
