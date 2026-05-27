# ruff: noqa: F821
from tools.web_search.trusted import contracts as srch
from tools.web_search.trusted.effects import web_search

import clauz3


@clauz3.guarantee(srch.query_length_at_most(8))
def main() -> None:
    web_search("this query is much longer than eight characters")
