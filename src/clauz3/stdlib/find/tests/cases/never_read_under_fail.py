# ruff: noqa: F821
from tools.find.trusted import contracts as find_rules
from tools.find.trusted.effects import find

import clauz3


@clauz3.guarantee(find_rules.never_read_under("/home/user/.ssh"))
def main() -> None:
    find("/home/user/.ssh", ["-name", "id_*"])
