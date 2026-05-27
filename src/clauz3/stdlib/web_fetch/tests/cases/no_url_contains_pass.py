# ruff: noqa: F821
from tools.web_fetch.trusted import contracts as web
from tools.web_fetch.trusted.effects import fetch_url

import clauz3


@clauz3.guarantee(web.no_url_contains("token="))
def main() -> None:
    fetch_url("https://api.github.com/repos/x/y")
