# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import balance, withdraw

import clauz3


@clauz3.guarantee(bank.max_spend(500))
def main() -> None:
    owed = balance("card")
    withdraw("card", owed)  # no cap: a large outstanding balance overspends
