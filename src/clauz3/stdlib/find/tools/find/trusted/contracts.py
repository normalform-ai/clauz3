"""Contract vocabulary for the find tool.

find is a read effect, so this module builds on the ``filesystem`` layer: the
path policies below apply that layer's read rule to find's own effect (whose
search root is recorded as the ``path`` field), via the ``reads_under``
builders it exposes for the purpose. find also adds its own search-specific
contract (``searches_at_most``).

Requires the ``filesystem`` trusted layer to be installed alongside find.
"""

from tools.filesystem.trusted import contracts as filesystem
from tools.find.trusted.effects import find

from clauz3.spec import ContractSpec, contract, effect

Find = effect(find)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about find (or other read) effects."""
    return filesystem.no_guarantees()


@contract
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every find walk has its root under ``root``."""
    return filesystem.reads_under(Find, root)


@contract
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no find walk has its root under ``prefix``."""
    return filesystem.never_reads_under(Find, prefix)


@contract
def searches_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` find walks occur."""
    return Find.count() <= count
