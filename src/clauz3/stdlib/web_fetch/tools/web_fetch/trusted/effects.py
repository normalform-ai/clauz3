"""Trusted web-fetch effect: a real (non-mock) HTTP GET.

Carries the ``net`` and ``fetch`` markers so the contracts in ``contracts.py``
can scope reachable URLs and bound the number of fetches.

The body uses ``urllib.request`` from the standard library — no third-party
HTTP dependency. The 30-second timeout is a defense against trivially-slow
responses; it is not a contract surface (the prover has no model of wall time).
"""

from urllib.request import Request, urlopen

import deal

DEFAULT_USER_AGENT = "clauz3-web-fetch/0.1"
DEFAULT_TIMEOUT_SECONDS = 30


@deal.pre(
    lambda url: url.startswith("http://") or url.startswith("https://"),
    message="url must start with http:// or https://",
)
@deal.has("net", "fetch", "global", "import", "trusted")
def fetch_url(url: str) -> str:
    """Issue an HTTP GET against ``url`` and return the response body as text.

    Uses a fixed UTF-8 decoding (with ``errors='replace'``) and a 30-second
    timeout. Per-call URL scheme is checked as a precondition; allowed host
    prefixes, fetch count, and exfil-style URL-content guards are agent-stated
    guarantees.
    """
    req = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:  # noqa: S310
        body = response.read()
    return body.decode("utf-8", errors="replace")
