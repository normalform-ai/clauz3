# ruff: noqa: F821
from tools.git.trusted import contracts as git_rules
from tools.git.trusted.effects import git

import clauz3


@clauz3.guarantee(git_rules.only_subcommands(["status", "add", "commit"]))
def main() -> None:
    git("add", ["-A"], "/repo")
    git("commit", ["-m", "work"], "/repo")
