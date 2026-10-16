# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import withdraw

import clauz3


# Same accumulator as aug_assign_loop_pass.py; the cap is 10 too tight.
@clauz3.guarantee(bank.max_spend(90))
def main() -> None:
    amount = 0
    for _ in range(4):
        amount += 10
        withdraw("checking", amount)
