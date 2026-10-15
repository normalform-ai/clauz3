# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy, remove_spend_cap

import clauz3


@clauz3.guarantee(cloud.cap_at_most(100))
@clauz3.guarantee(cloud.total_caps_at_most(150))
@clauz3.guarantee(cloud.all_services_capped_at_end())
def main() -> None:
    deploy("api", 50)
    remove_spend_cap("api")  # the end-state guarantee rejects this
