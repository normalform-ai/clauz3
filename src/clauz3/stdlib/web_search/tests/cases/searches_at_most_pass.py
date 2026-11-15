# ruff: noqa: F821
from cz3tools.web_search.trusted import contracts as srch
from cz3tools.web_search.trusted.effects import web_search

import clauz3


@clauz3.guarantee(srch.searches_at_most(2))
def main() -> None:
    web_search("foo")
    web_search("bar")
