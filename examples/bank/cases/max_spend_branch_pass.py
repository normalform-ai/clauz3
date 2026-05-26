# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import withdraw

import clauz3


@clauz3.guarantee(bank.max_spend(5))
def main(use_savings: bool) -> None:
    if use_savings:
        withdraw("savings", 5)
    else:
        withdraw("checking", 5)
