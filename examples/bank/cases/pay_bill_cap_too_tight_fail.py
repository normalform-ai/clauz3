# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import balance, withdraw

import clauz3


@clauz3.guarantee(bank.max_spend(499))
def main() -> None:
    owed = balance("card")
    if owed > 500:
        withdraw("card", 500)  # code can spend 500, tighter than the declared 499
    else:
        withdraw("card", owed)
