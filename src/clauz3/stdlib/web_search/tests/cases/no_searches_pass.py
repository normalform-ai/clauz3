# ruff: noqa: F821
from cz3tools.web_search.trusted import contracts as srch

import clauz3


@clauz3.guarantee(srch.no_searches())
def main() -> None:
    pass
