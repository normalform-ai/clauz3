# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import lock_door, order_grocery, unlock_door

import clauz3


# The classic errand: unlock the front door, fetch a delivery, lock up again.
# The door is unlocked mid-program but re-locked before the end, so the
# final-state guarantee holds even though an unlock event occurred.
@clauz3.guarantee(home.all_doors_locked_at_end())
@clauz3.guarantee(home.door_locked_at_end("front"))
def main() -> None:
    unlock_door("front")
    order_grocery("milk", 1, 8)
    lock_door("front")
