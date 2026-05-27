# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import pipette

import clauz3


@clauz3.guarantee(lims.only_plate("plate_42"))
def main() -> None:
    pipette("plate_42", "A1", 500, "ATP")
