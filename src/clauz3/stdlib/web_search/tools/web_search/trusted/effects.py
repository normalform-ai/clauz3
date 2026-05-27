"""Trusted web-search effect: a real (non-mock) search via a configurable backend.

The stdlib deliberately does not pick a search vendor. The body of
``web_search`` POSTs the query as JSON to a backend whose URL is taken from
the ``CLAUZ3_SEARCH_URL`` environment variable; an optional API key is read
from ``CLAUZ3_SEARCH_API_KEY``. The backend is expected to return a JSON
array of result URL strings (a single ``["https://...", ...]`` object).

This keeps the contract layer — which is the durable part of stdlib — vendor-
independent. The agent's guarantees about query length, query count, and
banned query substrings hold no matter which search service is on the other
end of the wire.

If ``CLAUZ3_SEARCH_URL`` is unset, the body raises ``RuntimeError`` at
``clauz3 run`` time. Proof never executes the body, so unset environment
during testing is fine.
"""

import json
import os
from urllib.request import Request, urlopen

import deal

DEFAULT_USER_AGENT = "clauz3-web-search/0.1"
DEFAULT_TIMEOUT_SECONDS = 30


@deal.pre(lambda query: len(query) > 0, message="query must be non-empty")
@deal.has("net", "search", "global", "import", "trusted")
def web_search(query: str) -> list[str]:
    """Search for ``query`` and return a list of result URLs as strings.

    The backend is configured by the ``CLAUZ3_SEARCH_URL`` env var; an
    optional API key in ``CLAUZ3_SEARCH_API_KEY`` is forwarded in the JSON
    payload. The backend must return a JSON array of URL strings.
    """
    endpoint = os.environ.get("CLAUZ3_SEARCH_URL")
    if not endpoint:
        raise RuntimeError(
            "CLAUZ3_SEARCH_URL is not set; cannot perform a real web search. "
            'Set it to a JSON endpoint that accepts {"query": str, ...} and '
            "returns a JSON array of result URL strings."
        )
    api_key = os.environ.get("CLAUZ3_SEARCH_API_KEY", "")
    payload = json.dumps({"query": query, "key": api_key}).encode("utf-8")
    req = Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:  # noqa: S310
        body = response.read()
    parsed = json.loads(body.decode("utf-8", errors="replace"))
    return [str(url) for url in parsed]
