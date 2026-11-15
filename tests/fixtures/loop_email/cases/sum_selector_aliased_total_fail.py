# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import charge, db_query_invoices
from qtrusted.rows import InvoiceRow


@clauz3.guarantee(contracts.total_amount_under(1000))
def main(sum_total_0: int, sum_lo_0_0: int, sum_hi_0_0: int, sum_total_1: int) -> None:
    # Parameters named like the promotion bound's internal symbols must not
    # alias them: the loop still charges up to 100 unbounded amounts.
    if sum_total_0 == 0 and sum_total_1 == 0:
        for inv in db_query_invoices("invoices"):
            charge(inv.amount)
