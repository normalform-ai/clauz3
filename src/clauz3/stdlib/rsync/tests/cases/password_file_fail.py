# ruff: noqa: F821
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync

import clauz3


@clauz3.guarantee(rsync_rules.syncs_at_most(1))
def main() -> None:
    rsync(
        "/repo/build/",
        "rsync://host/mod/",
        False,
        ["--password-file", "/home/me/.ssh/id_rsa"],
    )
