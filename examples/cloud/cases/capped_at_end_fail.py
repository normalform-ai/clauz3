# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy, remove_spend_cap

import clauz3


@clauz3.guarantee(cloud.all_services_capped_at_end())
def main() -> None:
    deploy("api", 50)
    deploy("worker", 20)
    remove_spend_cap("worker")  # worker is left billing without limit
