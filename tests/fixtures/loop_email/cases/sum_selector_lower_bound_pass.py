# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_at_least(0))
def main() -> None:
    # Every charge is nonnegative, so the smallest term is >= 0 and the
    # lower half of the promotion bound proves the total is >= 0.
    for inv in db_query_invoices("invoices"):
        if inv.amount >= 0:
            charge(inv.amount)
