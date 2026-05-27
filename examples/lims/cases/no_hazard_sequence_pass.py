# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import order_oligo

import clauz3


@clauz3.guarantee(lims.no_hazard_sequence("GATTACA"))
@clauz3.guarantee(lims.oligo_length_at_most(60))
def main() -> None:
    order_oligo("ACGTACGTACGT", 10)
