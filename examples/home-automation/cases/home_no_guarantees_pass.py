# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import order_grocery, set_thermostat, unlock_door

import clauz3


@clauz3.guarantee(home.no_guarantees())
def main() -> None:
    set_thermostat("attic", 88)
    unlock_door("front")
    order_grocery("caviar", 5, 400)
