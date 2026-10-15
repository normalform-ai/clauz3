# ruff: noqa: F821
from tools.web_fetch.trusted import contracts as web
from tools.web_fetch.trusted.effects import post_url

import clauz3


@clauz3.guarantee(web.no_posts())
def main() -> None:
    post_url("https://example.com/upload", "data")
