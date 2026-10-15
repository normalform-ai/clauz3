# ruff: noqa: F821
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync

import clauz3


@clauz3.guarantee(rsync_rules.never_dest_under("/etc"))
def main() -> None:
    rsync("/repo/build/", "/etc/cron.d/", False, ["-a"])
