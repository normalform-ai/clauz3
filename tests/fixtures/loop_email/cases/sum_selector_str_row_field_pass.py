# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_customer_invoices
from qtrusted.rows import CustomerInvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main() -> None:
    # A str row field adds a quantified string-domain given; the cap reader
    # must skip it rather than crash, so this still proves.
    for inv in db_query_customer_invoices("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
