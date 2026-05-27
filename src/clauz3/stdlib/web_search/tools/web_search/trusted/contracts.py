"""Web-search query policies built from generic trusted effect facts.

Searches are recorded under the ``net`` and ``search`` markers. These
contracts let an agent state how many searches may occur, how long each
query may be, and which substrings the query must (not) contain — the last
is a privacy / exfil guard with the same shape as ``web_fetch``'s
``no_url_contains``.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Search = effect("search")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about web-search effects."""
    return core_no_guarantees()


@contract
def no_searches() -> ContractSpec:
    """Guarantee the program issues no searches."""
    return Search.empty()


@contract
def searches_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` searches occur."""
    return Search.count() <= count


@contract
def query_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every search query is at most ``max_chars`` long.

    Bounds query size to prevent oversized smuggling of local data into the
    outbound search request.
    """
    return Search.all(lambda e: len(e.query) <= max_chars)


@contract
def no_query_contains(substring: str) -> ContractSpec:
    """Guarantee no search query contains ``substring``.

    Privacy / exfil guard with the same shape as ``web_fetch``'s
    ``no_url_contains``: an agent asked to look something up cannot smuggle
    a local secret into the query string.
    """
    return Search.all(lambda e: substring not in e.query)
