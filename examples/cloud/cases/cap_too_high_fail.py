# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy

import clauz3


@clauz3.guarantee(cloud.cap_at_most(100))
def main() -> None:
    deploy("api", 50)
    deploy("worker", 5000)  # one service's cap breaks the per-service bound
