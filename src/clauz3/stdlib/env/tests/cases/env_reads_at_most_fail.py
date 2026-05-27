# ruff: noqa: F821
from tools.env.trusted import contracts as envc
from tools.env.trusted.effects import read_env

import clauz3


@clauz3.guarantee(envc.env_reads_at_most(1))
def main() -> None:
    read_env("FOO")
    read_env("BAR")
