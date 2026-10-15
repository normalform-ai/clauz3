"""Path and call-count policies for the text read effect."""

from tools.filesystem.trusted import contracts as filesystem
from tools.text.trusted.effects import read_line

from clauz3.spec import ContractSpec, contract, effect

Read = effect(read_line)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about text reads."""
    return filesystem.no_guarantees()


@contract
def only_read_under(root: str) -> ContractSpec:
    """Every read_line call has a path beginning with root."""
    return filesystem.reads_under(Read, root)


@contract
def never_read_under(prefix: str) -> ContractSpec:
    """No read_line call has a path beginning with prefix."""
    return filesystem.never_reads_under(Read, prefix)


@contract
def reads_at_most(count: int) -> ContractSpec:
    """At most count read_line calls occur."""
    return Read.count() <= count
