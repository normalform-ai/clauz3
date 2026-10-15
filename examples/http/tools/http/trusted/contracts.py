"""HTTP request policies built from generic trusted effect facts.

``Request = effect(http_get, http_post)`` explicitly groups the two trusted
request functions, regardless of method. ``Post = effect(http_post)`` selects
only POST calls, which lets a contract constrain them separately.

The url-prefix check compiles to ``z3.PrefixOf`` via the relation-lambda
``str.startswith`` support in ``clauz3.spec``.
"""

from tools.http.trusted.effects import http_get, http_post

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Request = effect(http_get, http_post)
Post = effect(http_post)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about HTTP effects."""
    return core_no_guarantees()


@contract
def host_only(prefix: str) -> ContractSpec:
    """Guarantee every HTTP request url starts with ``prefix``."""
    return Request.all(lambda e: e.url.startswith(prefix))


@contract
def no_posts() -> ContractSpec:
    """Guarantee the program makes no HTTP POST request."""
    return Post.empty()
