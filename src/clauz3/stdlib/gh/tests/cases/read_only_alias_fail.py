# ruff: noqa: F821
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh

import clauz3


@clauz3.guarantee(gh_rules.read_only())
def main() -> None:
    # "co" is a local alias for "pr checkout", not a built-in command.
    gh("co", "view", [], "cmungall/agent-deal")
