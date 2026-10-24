# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(999))
def main() -> None:
    # 100 invoices of exactly 10 reach 1000, so a 999 cap must not prove.
    for inv in db_query_invoices("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
