# ruff: noqa: F821
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync

import clauz3


@clauz3.guarantee(rsync_rules.no_syncs())
def main() -> None:
    rsync("/repo/build/", "/backup/build/", False, ["-a"])
