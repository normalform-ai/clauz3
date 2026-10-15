# ruff: noqa: F821
from tools.git.trusted import contracts as git_rules
from tools.git.trusted.effects import git

import clauz3


@clauz3.guarantee(git_rules.runs_at_most(2))
def main() -> None:
    git("status", [], "/repo")
    git("log", [], "/repo")
    git("diff", [], "/repo")
