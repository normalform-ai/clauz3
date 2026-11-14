# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


def pay(inv: InvoiceRow) -> None:
    if inv.amount <= 10:
        charge(inv.amount)


@clauz3.guarantee(contracts.total_amount_under(1000))
def main() -> None:
    # The charge is emitted inside a helper, which inherits the loop's frame.
    for inv in db_query_invoices("invoices"):
        pay(inv)
