# ruff: noqa: F821
from tools.env.trusted import contracts as envc
from tools.env.trusted.effects import read_env

import clauz3


@clauz3.guarantee(envc.only_vars(["GITHUB_REPO", "OPENAI_BASE_URL"]))
def main() -> None:
    read_env("GITHUB_REPO")
    read_env("OPENAI_BASE_URL")
