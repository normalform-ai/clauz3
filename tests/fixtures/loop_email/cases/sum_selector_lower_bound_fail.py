# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_at_least(1))
def main() -> None:
    # The query can return no rows, so the total can be 0: >= 1 must fail.
    for inv in db_query_invoices("invoices"):
        if inv.amount >= 0:
            charge(inv.amount)
