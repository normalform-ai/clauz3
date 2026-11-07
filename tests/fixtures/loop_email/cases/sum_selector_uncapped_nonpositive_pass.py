# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices_uncapped
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main() -> None:
    # No length cap, so only the nonlinear n * max bound exists; with every
    # term <= 0 it still settles quickly and proves.
    for inv in db_query_invoices_uncapped("invoices"):
        if inv.amount <= 0:
            charge(inv.amount)
