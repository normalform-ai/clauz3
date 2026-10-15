# ruff: noqa
import clauz3
from tools.trading.trusted import contracts as trading
from tools.trading.trusted.effects import list_approvals, sell_shares
from tools.trading.trusted.schemas import ApprovalRow


# A KNOWN, DOCUMENTED HOLE, kept as a case so it is a tested fact rather than a
# prose claim. See "Row mixing" in docs/examples/trading.md.
#
# `stock_from` and `shares_from` are independent clauses, and column-ref
# equality is a structural match on `<field>(Select(<approval array>, ?))` that
# ignores the index. So every stock sold is an approved stock, every quantity
# sold is an approved quantity, and they need not come from the same row: this
# sells row 0's approved quantity for *every* approved name.
#
# It proves today. If a future prover change adds row-identity binding, this
# case starts failing — which is the signal to close the hole in the docs, the
# README row, and the contracts module docstring in the same commit.
@clauz3.guarantee(trading.approvals_only_desk("equities"))
@clauz3.guarantee(trading.approvals_only_where({"approved": True}))
@clauz3.guarantee(trading.stock_from(ApprovalRow, "stock"))
@clauz3.guarantee(trading.shares_from(ApprovalRow, "shares"))
def main() -> None:
    rows = list_approvals("equities", where={"approved": True})
    for row in rows:
        sell_shares(row.stock, rows[0].shares)
