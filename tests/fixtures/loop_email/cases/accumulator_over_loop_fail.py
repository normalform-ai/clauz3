# ruff: noqa: F821
#
# Issue #123. `total` holds one row's contribution, bounded by the in-loop
# test, so `total_amount_under(10)` used to prove. The runtime sum over up to
# 100 rows is not bounded by 10: five invoices of 4 charge 20.
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices

import clauz3


@clauz3.guarantee(contracts.total_amount_under(10))
def main() -> None:
    total = 0
    for inv in db_query_invoices("invoices"):
        if inv.amount <= 10:
            total = total + inv.amount
    charge(total)
