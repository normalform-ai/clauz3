# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices_uncapped
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main() -> None:
    # No length cap: 1001 rows of 1 exceed 1000, so a counterexample is found
    # without needing the linear relaxation.
    for inv in db_query_invoices_uncapped("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
