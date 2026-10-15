# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy, remove_spend_cap

import clauz3


@clauz3.guarantee(cloud.never_remove_caps())
def main() -> None:
    deploy("api", 50)
    remove_spend_cap("api")
    deploy("api", 50)  # restored, but the cap was still removed meanwhile
