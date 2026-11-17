"""Web-fetch URL and method policies built from generic trusted effect facts.

``Fetch`` covers both HTTP effects, GET and POST; ``Post`` covers POST
alone. These
contracts let an agent state which URLs may be fetched, how many fetches may
occur, whether POSTs are forbidden, and which substrings the URL itself must
(not) contain — the last is the genuinely novel exfil guard a typical HTTP
client cannot statically prove.
"""

from tools.web_fetch.trusted.effects import fetch_url, post_url

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Fetch = effect(fetch_url, post_url)
Post = effect(post_url)


def _allowed_prefix(prefix: str) -> str:
    """Refuse an allow-list prefix that stops inside the host part of a URL.

    The comparison is on the text of the URL, so ``https://api.github.com``
    is also the start of ``https://api.github.com.evil.example/`` and of
    ``https://api.github.com@evil.example/``: the allowed address would not
    name one site. A prefix that names a host must end it with ``/``.
    Raising here makes the proof a refusal, not a pass that means less than
    it says. ``https://`` alone names no host and is left alone.

    >>> _allowed_prefix("https://api.github.com/")
    'https://api.github.com/'
    >>> _allowed_prefix("https://a.example")
    Traceback (most recent call last):
    ...
    ValueError: URL prefix 'https://a.example' must end its host name with '/'
    """
    _scheme, separator, rest = prefix.partition("://")
    if separator and rest and "/" not in rest:
        raise ValueError(f"URL prefix {prefix!r} must end its host name with '/'")
    return prefix


@contract(description="No restrictions are imposed on web requests.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about web requests."""
    return core_no_guarantees()


@contract(description="The web request tools fetch no pages and send no data.")
def no_fetches() -> ContractSpec:
    """Guarantee the program makes no web requests (neither GET nor POST)."""
    return Fetch.empty()


@contract(
    description=(
        "The web request tools never post data to a website; they can still "
        "fetch pages, and a page address can itself carry data."
    )
)
def no_posts() -> ContractSpec:
    """Guarantee the program never posts data to a website (no HTTP POSTs)."""
    return Post.empty()


@contract(
    description=(
        "Every web request is sent to an address starting with {prefix} "
        "(the site may still redirect elsewhere)."
    )
)
def only_fetch_under(prefix: str) -> ContractSpec:
    """Guarantee every requested web address starts with ``prefix``.

    Use fully-qualified prefixes (``"https://api.github.com/repos/"``) to
    bound both scheme + host + path. A prefix that names a host must end it
    with ``/``: ``"https://api.github.com"`` is refused, because
    ``https://api.github.com.evil.example/`` starts with it too. A bare
    scheme like ``"https://"`` works too but admits any host; combine with
    multiple ``only_fetch_under`` for disjunctive host allowlists. Covers
    GETs and POSTs. Only the requested URL is checked: the HTTP client
    follows redirects, which are not.
    """
    _allowed_prefix(prefix)
    return Fetch.all(lambda e: e.url.startswith(prefix))


@contract(
    description=(
        "No web request is sent to an address starting with {prefix} "
        "(redirects are not checked)."
    )
)
def never_fetch_under(prefix: str) -> ContractSpec:
    """Guarantee no requested web address starts with ``prefix``.

    Covers GETs and POSTs. Only the requested URL is checked: the HTTP client
    follows redirects, which are not.
    """
    return Fetch.all(lambda e: not e.url.startswith(prefix))


@contract(
    description=(
        "Every web request is sent to an address starting with https:// "
        "(redirects are not checked)."
    )
)
def https_only() -> ContractSpec:
    """Guarantee every requested web address uses HTTPS.

    Only the requested URL is checked: the HTTP client follows redirects,
    which are not.
    """
    return Fetch.all(lambda e: e.url.startswith("https://"))


@contract(description="The web request tools make at most {count} requests.")
def fetches_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` web requests (GETs plus POSTs) occur."""
    return Fetch.count() <= count


@contract(
    description=(
        "No requested web address contains the exact text {substring}; data "
        "posted to a website is not checked."
    )
)
def no_url_contains(substring: str) -> ContractSpec:
    """Guarantee no requested web address contains the given text.

    Useful as an exfiltration guard: an agent asked to look up data must not
    smuggle local secrets into the URL path or query string. For example,
    ``no_url_contains("BEGIN PRIVATE KEY")`` rejects any URL with a literal
    key dumped into a query parameter. The comparison is a case-sensitive
    substring test of the URL only; a POST body is not checked (stack
    ``no_posts()`` to forbid POSTs).
    """
    return Fetch.all(lambda e: substring not in e.url)


@contract(
    description="Every requested web address is at most {max_chars} characters long."
)
def url_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every requested web address is at most ``max_chars`` long.

    Bounds URL size to prevent oversized query-string smuggling. A POST body
    is not bounded.
    """
    return Fetch.all(lambda e: len(e.url) <= max_chars)
