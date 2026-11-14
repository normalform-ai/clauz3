# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices3
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(89))
def main() -> None:
    # 3 * 3 charges of 10 reach 90, so an 89 cap must fail. A sum or max of
    # the per-loop caps instead of their product would wrongly prove it.
    batches = db_query_invoices3("batches")
    invoices = db_query_invoices3("invoices")
    for batch in batches:
        for inv in invoices:
            if inv.amount <= 10:
                charge(inv.amount)
