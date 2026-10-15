# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy

import clauz3


@clauz3.guarantee(cloud.cap_at_most(50))
@clauz3.guarantee(cloud.total_caps_at_most(150))
def main() -> None:
    deploy("api", 50)
    deploy("worker", 50)
    deploy("scheduler", 50)
