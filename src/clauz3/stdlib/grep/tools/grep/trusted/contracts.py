"""Contract vocabulary for the grep tool.

grep is a read effect, so this module builds directly on the ``filesystem``
layer: the path policies below delegate to the filesystem read contracts, and
a grep call is governed by them because it shares the ``read`` marker. grep
also adds its own search-specific contracts (``searches_at_most``,
``only_pattern``).

Requires the ``filesystem`` trusted layer to be installed alongside grep.
"""

from tools.filesystem.trusted import contracts as filesystem

from clauz3.spec import ContractSpec, contract, effect

Grep = effect("grep")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about grep (or other read) effects."""
    return filesystem.no_guarantees()


@contract
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every grep search reads a file under ``root``."""
    return filesystem.only_read_under(root)


@contract
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no grep search reads a file under ``prefix``."""
    return filesystem.never_read_under(prefix)


@contract
def searches_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` grep searches occur."""
    return Grep.count() <= count


@contract
def only_pattern(pattern: str) -> ContractSpec:
    """Guarantee every grep search uses exactly this pattern."""
    return Grep.all(lambda e: e.pattern == pattern)
