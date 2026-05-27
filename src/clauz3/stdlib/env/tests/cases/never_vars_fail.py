# ruff: noqa: F821
from tools.env.trusted import contracts as envc
from tools.env.trusted.effects import read_env

import clauz3


@clauz3.guarantee(envc.never_vars(["AWS_SECRET_ACCESS_KEY"]))
def main() -> None:
    read_env("AWS_SECRET_ACCESS_KEY")
