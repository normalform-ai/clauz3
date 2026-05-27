# ruff: noqa: F821
from tools.web_fetch.trusted import contracts as web
from tools.web_fetch.trusted.effects import fetch_url

import clauz3


@clauz3.guarantee(web.never_fetch_under("https://internal.corp/"))
def main() -> None:
    fetch_url("https://internal.corp/private/data")
