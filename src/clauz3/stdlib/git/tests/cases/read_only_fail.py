# ruff: noqa: F821
from tools.git.trusted import contracts as git_rules
from tools.git.trusted.effects import git

import clauz3


@clauz3.guarantee(git_rules.read_only())
def main() -> None:
    git("commit", ["-m", "tweak"], "/repo")
