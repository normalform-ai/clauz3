# ruff: noqa: F821
from tools.env.trusted import contracts as envc
from tools.env.trusted.effects import read_env

import clauz3


@clauz3.guarantee(envc.never_var_prefix("AWS_"))
def main() -> None:
    read_env("GITHUB_REPO")
