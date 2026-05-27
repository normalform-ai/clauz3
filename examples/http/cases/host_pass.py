# ruff: noqa: F821
from tools.http.trusted import contracts as http
from tools.http.trusted.effects import http_get

import clauz3


@clauz3.guarantee(http.host_only("https://example.com/"))
def main() -> None:
    http_get("https://example.com/data.json")
