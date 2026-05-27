"""Web-fetch URL policies built from generic trusted effect facts.

Fetches are recorded under the ``net`` and ``fetch`` markers. These contracts
let an agent state which URLs may be fetched, how many fetches may occur, and
which substrings the URL itself must (not) contain — the last is the genuinely
novel exfil guard a typical HTTP client cannot statically prove.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Fetch = effect("fetch")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about web-fetch effects."""
    return core_no_guarantees()


@contract
def no_fetches() -> ContractSpec:
    """Guarantee the program issues no HTTP fetches."""
    return Fetch.empty()


@contract
def only_fetch_under(prefix: str) -> ContractSpec:
    """Guarantee every fetched URL starts with ``prefix``.

    Use fully-qualified prefixes (``"https://api.github.com/repos/"``) to
    bound both scheme + host + path. A bare scheme like ``"https://"`` works
    too but admits any host; combine with multiple ``only_fetch_under`` for
    disjunctive host allowlists.
    """
    return Fetch.all(lambda e: e.url.startswith(prefix))


@contract
def never_fetch_under(prefix: str) -> ContractSpec:
    """Guarantee no fetched URL starts with ``prefix``."""
    return Fetch.all(lambda e: not e.url.startswith(prefix))


@contract
def https_only() -> ContractSpec:
    """Guarantee every fetched URL uses HTTPS."""
    return Fetch.all(lambda e: e.url.startswith("https://"))


@contract
def fetches_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` fetches occur."""
    return Fetch.count() <= count


@contract
def no_url_contains(substring: str) -> ContractSpec:
    """Guarantee no fetched URL contains ``substring`` as a substring.

    Useful as an exfiltration guard: an agent asked to look up data must not
    smuggle local secrets into the URL path or query string. For example,
    ``no_url_contains("BEGIN PRIVATE KEY")`` rejects any URL with a literal
    key dumped into a query parameter.
    """
    return Fetch.all(lambda e: substring not in e.url)


@contract
def url_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every fetched URL is at most ``max_chars`` long.

    Bounds URL size to prevent oversized query-string smuggling.
    """
    return Fetch.all(lambda e: len(e.url) <= max_chars)
