# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import order_grocery, set_thermostat, unlock_door

import clauz3


@clauz3.guarantee(home.temp_between(55, 65))
@clauz3.guarantee(home.never_unlock_door("front"))
@clauz3.guarantee(home.only_unlock_doors(["back"]))
@clauz3.guarantee(home.grocery_budget(30))
@clauz3.guarantee(home.grocery_items_at_most(2))
def main() -> None:
    set_thermostat("living_room", 58)
    set_thermostat("bedroom", 60)
    unlock_door("back")
    order_grocery("milk", 1, 8)
    order_grocery("bread", 1, 5)
