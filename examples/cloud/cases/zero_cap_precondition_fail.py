# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy

import clauz3


@clauz3.guarantee(cloud.cap_at_most(100))
def main() -> None:
    deploy("api", 0)  # refutes deploy's own precondition, not the guarantee
