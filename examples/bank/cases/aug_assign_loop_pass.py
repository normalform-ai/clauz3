# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import withdraw

import clauz3


# `amount` is 10, 20, 30, 40 across the unrolled loop, so the withdrawals
# total exactly 100. `range(4)` is unrolled, so the accumulator is modelled
# per iteration; over trusted query results the same body is refused.
@clauz3.guarantee(bank.max_spend(100))
def main() -> None:
    amount = 0
    for _ in range(4):
        amount += 10
        withdraw("checking", amount)
