# ruff: noqa: F821
from cz3tools.web_fetch.trusted import contracts as web
from cz3tools.web_fetch.trusted.effects import post_url

import clauz3


@clauz3.guarantee(web.only_fetch_under("https://api.example.com/"))
@clauz3.guarantee(web.fetches_at_most(1))
def main() -> None:
    post_url("https://api.example.com/upload", "data")
