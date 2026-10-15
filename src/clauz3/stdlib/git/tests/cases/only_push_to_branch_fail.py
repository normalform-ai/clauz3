# ruff: noqa: F821
from tools.git.trusted import contracts as git_rules
from tools.git.trusted.effects import git_push

import clauz3


@clauz3.guarantee(git_rules.only_push_to_branch("main"))
def main() -> None:
    git_push("origin", "wip", "/repo")
