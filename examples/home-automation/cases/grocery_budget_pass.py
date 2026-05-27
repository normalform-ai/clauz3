# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import order_grocery

import clauz3


@clauz3.guarantee(home.grocery_budget(50))
def main() -> None:
    order_grocery("milk", 2, 8)
    order_grocery("bread", 1, 5)
    order_grocery("eggs", 1, 6)
