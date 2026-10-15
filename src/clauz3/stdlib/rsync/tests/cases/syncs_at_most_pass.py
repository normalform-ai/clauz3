# ruff: noqa: F821
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync

import clauz3


@clauz3.guarantee(rsync_rules.syncs_at_most(2))
def main() -> None:
    rsync("/repo/build/", "/backup/build/", False, ["-a"])
    rsync("/repo/dist/", "/backup/dist/", False, ["-a"])
