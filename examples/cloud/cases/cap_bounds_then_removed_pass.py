# ruff: noqa: F821
from tools.cloud.trusted import contracts as cloud
from tools.cloud.trusted.effects import deploy, remove_spend_cap

import clauz3


# Proves: cap bounds only constrain deploy caps. They do not see the later
# removal, so the service ends up billing without limit. See the paired
# _fail case for the guarantee that closes this gap.
@clauz3.guarantee(cloud.cap_at_most(100))
@clauz3.guarantee(cloud.total_caps_at_most(150))
def main() -> None:
    deploy("api", 50)
    remove_spend_cap("api")
