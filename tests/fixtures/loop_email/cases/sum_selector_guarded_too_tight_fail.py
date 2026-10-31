# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices3
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(29))
def main() -> None:
    # Three invoices of exactly 10 reach 30, so a 29 cap must not prove. The
    # 3-row query keeps the counterexample well inside the solver timeout.
    for inv in db_query_invoices3("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
