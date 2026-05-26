"""Filesystem path policies built from generic trusted effect facts.

Reads are recorded under the ``read`` marker and writes under the ``write``
marker (see ``effects.py``). These contracts let an agent state *where* it is
allowed to read and write, as path-prefix policies. The prefix checks use the
relation-lambda ``str.startswith`` support in ``clauz3.spec``.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Read = effect("read")
Write = effect("write")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about filesystem effects."""
    return core_no_guarantees()


@contract
def read_only() -> ContractSpec:
    """Guarantee the program performs no filesystem writes."""
    return Write.empty()


@contract
def no_reads() -> ContractSpec:
    """Guarantee the program reads no files."""
    return Read.empty()


@contract
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every file read has a path under ``root``."""
    return Read.all(lambda e: e.path.startswith(root))


@contract
def only_write_under(root: str) -> ContractSpec:
    """Guarantee every file write has a path under ``root``."""
    return Write.all(lambda e: e.path.startswith(root))


@contract
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no file read has a path under ``prefix``."""
    return Read.all(lambda e: not e.path.startswith(prefix))


@contract
def never_write_under(prefix: str) -> ContractSpec:
    """Guarantee no file write has a path under ``prefix``."""
    return Write.all(lambda e: not e.path.startswith(prefix))


@contract
def writes_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` file writes occur."""
    return Write.count() <= count
