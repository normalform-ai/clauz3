# ruff: noqa: F821
from cz3tools.web_fetch.trusted import contracts as web
from cz3tools.web_fetch.trusted.effects import fetch_url

import clauz3


@clauz3.guarantee(web.never_fetch_under("https://internal.corp/"))
def main() -> None:
    fetch_url("https://api.github.com/repos/x/y")
