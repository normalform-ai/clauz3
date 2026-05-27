# ruff: noqa: F821
from tools.http.trusted import contracts as http
from tools.http.trusted.effects import http_get

import clauz3


@clauz3.guarantee(http.no_posts())
def main() -> None:
    http_get("https://example.com/")
