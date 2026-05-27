# ruff: noqa: F821
from tools.lims.trusted import contracts as lims
from tools.lims.trusted.effects import submit_protocol

import clauz3


@clauz3.guarantee(lims.total_runtime_at_most(8))
def main() -> None:
    submit_protocol("qPCR-1", "plate_42", 3)
    submit_protocol("HPLC-3", "plate_42", 4)
