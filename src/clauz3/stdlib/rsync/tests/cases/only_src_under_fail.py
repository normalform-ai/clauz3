# ruff: noqa: F821
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync

import clauz3


@clauz3.guarantee(rsync_rules.only_src_under("/repo"))
def main() -> None:
    rsync("/home/user/.ssh/", "/backup/keys/", False, ["-a"])
