# ruff: noqa: F821
from tools.git.trusted import contracts as git_rules
from tools.git.trusted.effects import git

import clauz3


@clauz3.guarantee(git_rules.never_subcommands(["reset", "clean", "rebase"]))
def main() -> None:
    git("status", [], "/repo")
    git("commit", ["-m", "ok"], "/repo")
