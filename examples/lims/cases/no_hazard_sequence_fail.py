# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import order_oligo

import clauz3


@clauz3.guarantee(lims.no_hazard_sequence("GATTACA"))
def main() -> None:
    order_oligo("AAAGATTACATTT", 5)
