# ruff: noqa: F821
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync

import clauz3


@clauz3.guarantee(rsync_rules.no_delete())
@clauz3.guarantee(rsync_rules.never_dest_under("/precious"))
def main() -> None:
    rsync("/precious/x", "--delete", False, ["-a", "/data/y"])
