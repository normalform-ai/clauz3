# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main() -> None:
    # The inner query runs once per outer row, so its length depends on the
    # outer index and the row count is a sum, not a product: refused.
    for outer in db_query_invoices("batches"):
        for inv in db_query_invoices("invoices"):
            if inv.amount <= 0:
                charge(inv.amount)
