# ruff: noqa: F821
#
# Issue #123. The accumulator sits one loop deeper than the name it writes.
# Refusing loop-carried state is a property of the whole body, not of its top
# level.
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices

import clauz3


@clauz3.guarantee(contracts.total_amount_under(10))
def main() -> None:
    total = 0
    for _inv in db_query_invoices("invoices"):
        for other in db_query_invoices("invoices"):
            total = total + other.amount
    charge(total)
