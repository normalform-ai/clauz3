# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import withdraw

import clauz3


@clauz3.guarantee(bank.only_account("checking"))
def main() -> None:
    withdraw("savings", 1)
