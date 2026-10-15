# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import make_dir

import clauz3


@clauz3.guarantee(cu.only_create_under("/repo/build"))
def main() -> None:
    make_dir("/etc/cron.d")
