# ruff: noqa: F821
from cz3tools.web_fetch.trusted import contracts as web
from cz3tools.web_fetch.trusted.effects import fetch_url

import clauz3


@clauz3.guarantee(web.fetches_at_most(1))
def main() -> None:
    fetch_url("https://example.com/a")
    fetch_url("https://example.com/b")
