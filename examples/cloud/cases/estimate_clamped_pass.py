# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy, estimate_monthly_cost

import clauz3


@clauz3.guarantee(cloud.cap_at_most(100))
def main() -> None:
    headroom = 2 * estimate_monthly_cost("api")
    if headroom > 100:
        deploy("api", 100)
    elif headroom > 0:
        deploy("api", headroom)
    else:
        deploy("api", 1)
