# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main(enabled: bool) -> None:
    # The query only runs under a branch, so its len(result) <= 100 given is
    # conditional; the cap reader must still find it to stay linear.
    if enabled:
        for inv in db_query_invoices("invoices"):
            if inv.amount <= 10:
                charge(inv.amount)
