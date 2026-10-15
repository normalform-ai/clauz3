# ruff: noqa: F821
from tools.find.trusted import contracts as find_rules
from tools.find.trusted.effects import find

import clauz3


@clauz3.guarantee(find_rules.searches_at_most(2))
def main() -> None:
    find("/repo/src", ["-name", "*.py"])
    find("/repo/tests", ["-name", "*.py"])
