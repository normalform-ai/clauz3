# ruff: noqa: F821
from tools.grep.trusted import contracts as grep_rules
from tools.grep.trusted.effects import grep

import clauz3


@clauz3.guarantee(grep_rules.only_pattern("TODO"))
def main() -> None:
    grep("TODO", "/repo/a.py")
    grep("TODO", "/repo/b.py")
