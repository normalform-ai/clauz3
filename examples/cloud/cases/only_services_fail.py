# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy

import clauz3


@clauz3.guarantee(cloud.only_services(["api", "worker"]))
def main() -> None:
    deploy("api", 50)
    deploy("crypto-miner", 20)  # not in the allowlist
