# ruff: noqa: F821
from tools.web_fetch.trusted import contracts as web
from tools.web_fetch.trusted.effects import fetch_url

import clauz3


@clauz3.guarantee(web.url_length_at_most(60))
def main() -> None:
    fetch_url("https://example.com/short")
