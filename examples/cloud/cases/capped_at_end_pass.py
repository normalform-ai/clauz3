# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy, remove_spend_cap

import clauz3


@clauz3.guarantee(cloud.all_services_capped_at_end())
def main() -> None:
    # Lift the cap for a one-off backfill, then redeploy with a cap.
    deploy("api", 50)
    remove_spend_cap("api")
    deploy("api", 50)
