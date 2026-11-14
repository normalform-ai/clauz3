# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(100000))
def main() -> None:
    # Two independent loops of <= 100 rows each, every charge <= 10: the row
    # count and the cap are both products, 100 * 100 * 10 = 100000.
    batches = db_query_invoices("batches")
    invoices = db_query_invoices("invoices")
    for batch in batches:
        for inv in invoices:
            if inv.amount <= 10:
                charge(inv.amount)
