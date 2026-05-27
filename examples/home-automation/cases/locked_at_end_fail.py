# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import lock_door, unlock_door

import clauz3


# Unlocks the back door and re-locks it, but unlocks the front door and never
# re-locks it. ``all_doors_locked_at_end`` must fail: the final state has the
# front door open. A balanced unlock/lock *count* would not catch this — the
# trace has two unlocks and one lock, but even an equal count could leave the
# wrong door open. Only the final-state fluent rules it out.
@clauz3.guarantee(home.all_doors_locked_at_end())
def main() -> None:
    unlock_door("back")
    lock_door("back")
    unlock_door("front")
