# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import balance, withdraw

import clauz3


@clauz3.guarantee(bank.max_spend(500))
@clauz3.guarantee(bank.only_account("card"))
def main() -> None:
    owed = balance("card")
    if owed > 500:
        withdraw("card", 500)
    else:
        withdraw("card", owed)
