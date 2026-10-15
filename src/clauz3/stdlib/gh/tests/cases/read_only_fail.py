# ruff: noqa: F821
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh

import clauz3


@clauz3.guarantee(gh_rules.read_only())
def main() -> None:
    gh("pr", "merge", ["42"], "cmungall/agent-deal")
