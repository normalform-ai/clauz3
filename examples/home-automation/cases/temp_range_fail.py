# ruff: noqa: F821
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import set_thermostat

import clauz3


@clauz3.guarantee(home.temp_between(60, 72))
def main() -> None:
    set_thermostat("living_room", 68)
    set_thermostat("bedroom", 85)
