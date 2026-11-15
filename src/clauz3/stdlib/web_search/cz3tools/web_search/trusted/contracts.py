"""Web-search query policies built from generic trusted effect facts.

Searches are recorded as the ``web_search`` effect. These
contracts let an agent state how many searches may occur, how long each
query may be, and which substrings the query must (not) contain — the last
is a privacy / exfil guard with the same shape as ``web_fetch``'s
``no_url_contains``.
"""

from cz3tools.web_search.trusted.effects import web_search

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Search = effect(web_search)


@contract(description="No restrictions are imposed on web searches.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about web searches."""
    return core_no_guarantees()


@contract(description="No web searches are made.")
def no_searches() -> ContractSpec:
    """Guarantee the program makes no web searches."""
    return Search.empty()


@contract(description="At most {count} web searches are made.")
def searches_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` searches occur."""
    return Search.count() <= count


@contract(description="Every web search query is at most {max_chars} characters long.")
def query_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every search query is at most ``max_chars`` long.

    Bounds query size to prevent oversized smuggling of local data into the
    outbound search request.
    """
    return Search.all(lambda e: len(e.query) <= max_chars)


@contract(description="No web search query contains the exact text {substring}.")
def no_query_contains(substring: str) -> ContractSpec:
    """Guarantee no search query contains the given text.

    Privacy / exfil guard with the same shape as ``web_fetch``'s
    ``no_url_contains``: an agent asked to look something up cannot smuggle
    a local secret into the query string. The comparison is a case-sensitive
    substring test.
    """
    return Search.all(lambda e: substring not in e.query)
