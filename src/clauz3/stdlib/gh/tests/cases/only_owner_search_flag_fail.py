# ruff: noqa: F821
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh

import clauz3


@clauz3.guarantee(gh_rules.only_owner("cmungall"))
def main() -> None:
    gh("issue", "list", ["-S", "repo:other/victim"], "cmungall/agent-deal")
