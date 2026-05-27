# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import pipette, submit_protocol

import clauz3


@clauz3.guarantee(lims.only_plate("plate_42"))
@clauz3.guarantee(lims.reagent_volume_at_most("ATP", 500))
@clauz3.guarantee(lims.only_instruments(["qPCR-1"]))
@clauz3.guarantee(lims.total_runtime_at_most(10))
def main() -> None:
    for _ in range(8):
        pipette("plate_42", "A1", 50, "ATP")
    submit_protocol("qPCR-1", "plate_42", 4)
