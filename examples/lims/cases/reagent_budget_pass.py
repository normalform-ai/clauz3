# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import pipette

import clauz3


@clauz3.guarantee(lims.reagent_volume_at_most("ATP", 200))
def main() -> None:
    pipette("plate_42", "A1", 80, "ATP")
    pipette("plate_42", "A2", 80, "ATP")
    pipette("plate_42", "B1", 20, "tRNA")
