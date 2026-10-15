# ruff: noqa: F821
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh

import clauz3


@clauz3.guarantee(gh_rules.never_actions(["merge", "close", "delete"]))
def main() -> None:
    gh("pr", "merge", ["42"], "cmungall/agent-deal")
