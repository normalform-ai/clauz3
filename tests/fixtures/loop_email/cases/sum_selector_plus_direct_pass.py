# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main() -> None:
    # A direct charge outside the loop adds to the loop's promotion bound.
    charge(100)
    for inv in db_query_invoices("invoices"):
        if inv.amount <= 9:
            charge(inv.amount)
