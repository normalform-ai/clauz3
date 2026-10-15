# ruff: noqa: F821
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh

import clauz3


@clauz3.guarantee(gh_rules.only_commands(["pr", "issue"]))
def main() -> None:
    gh("pr", "view", ["42"], "cmungall/agent-deal")
    gh("issue", "list", [], "cmungall/agent-deal")
