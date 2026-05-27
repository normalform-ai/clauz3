# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import order_oligo, pipette, submit_protocol

import clauz3


@clauz3.guarantee(lims.no_guarantees())
def main() -> None:
    pipette("plate_99", "Z9", 200, "EtOH")
    submit_protocol("MassSpec-7", "plate_99", 12)
    order_oligo("ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT", 500)
