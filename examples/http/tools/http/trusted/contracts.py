"""HTTP request policies built from generic trusted effect facts.

Both ``http_get`` and ``http_post`` carry deal's shared ``http`` marker (see
``effects.py``), so a single ``http`` relation ranges over every request the
program makes, regardless of method. ``http_post`` additionally matches its own
function-name relation, which lets a contract speak about POSTs specifically.

The url-prefix check compiles to ``z3.PrefixOf`` via the relation-lambda
``str.startswith`` support in ``clauz3.spec``.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Request = effect("http")
Post = effect("http_post")


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
