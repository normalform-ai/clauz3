# ruff: noqa: F821
from tools.bank.trusted import contracts as bank
from tools.bank.trusted.effects import withdraw

import clauz3


@clauz3.guarantee(bank.max_spend(100))
def main() -> None:
    for _ in range(5):
        withdraw("checking", 10)
