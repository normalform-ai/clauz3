# ruff: noqa: F821
from tools.env.trusted import contracts as envc

import clauz3


@clauz3.guarantee(envc.no_env_reads())
def main() -> None:
    pass
