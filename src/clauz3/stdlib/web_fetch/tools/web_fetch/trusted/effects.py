"""Trusted web-fetch effects: real (non-mock) HTTP GET and POST.

Both effects are covered by the ``Fetch`` relation, so the contracts in
``contracts.py`` can scope reachable URLs and bound the number of fetches.

The body uses ``urllib.request`` from the standard library — no third-party
HTTP dependency. The 30-second timeout is a defense against trivially-slow
responses; it is not a contract surface (the prover has no model of wall time).
"""

from urllib.request import Request, urlopen

import deal

import clauz3
from clauz3._path_safety import url_has_no_parent_segments

DEFAULT_USER_AGENT = "clauz3-web-fetch/0.1"
DEFAULT_TIMEOUT_SECONDS = 30


@clauz3.requires(
    lambda url: url_has_no_parent_segments(url),
    message="url must not contain parent-directory segments",
)
@clauz3.requires(
    lambda url: url.startswith("http://") or url.startswith("https://"),
    message="url must start with http:// or https://",
)
@deal.has("network", "io", "global", "import")
@clauz3.trusted
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


@clauz3.requires(
    lambda url, body: url_has_no_parent_segments(url),
    message="url must not contain parent-directory segments",
)
@clauz3.requires(
    lambda url, body: url.startswith("http://") or url.startswith("https://"),
    message="url must start with http:// or https://",
)
@deal.has("network", "io", "global", "import")
@clauz3.trusted
def post_url(url: str, body: str) -> str:
    """Issue an HTTP POST against ``url`` and return the response body as text.

    ``body`` is encoded as UTF-8 with a ``text/plain`` content type. URL scheme
    is checked as a precondition; ``contracts.no_posts()`` can forbid this
    effect entirely, while the shared URL/count contracts also apply because
    POSTs join GETs in the ``Fetch`` relation.
    """
    req = Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:  # noqa: S310
        response_body = response.read()
    return response_body.decode("utf-8", errors="replace")
