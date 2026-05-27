# ruff: noqa: F821
from tools.web_fetch.trusted import contracts as web

import clauz3


@clauz3.guarantee(web.no_fetches())
def main() -> None:
    pass
