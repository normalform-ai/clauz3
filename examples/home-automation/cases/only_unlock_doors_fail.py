# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import unlock_door

import clauz3


@clauz3.guarantee(home.only_unlock_doors(["back", "garage"]))
def main() -> None:
    unlock_door("back")
    unlock_door("front")
