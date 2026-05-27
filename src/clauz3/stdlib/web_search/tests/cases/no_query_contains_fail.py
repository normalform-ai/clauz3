# ruff: noqa: F821
from tools.web_search.trusted import contracts as srch
from tools.web_search.trusted.effects import web_search

import clauz3


@clauz3.guarantee(srch.no_query_contains("BEGIN PRIVATE KEY"))
def main() -> None:
    web_search("how to use BEGIN PRIVATE KEY in openssl")
