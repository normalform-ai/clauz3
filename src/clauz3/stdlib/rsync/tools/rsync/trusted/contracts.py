"""rsync destination and delete policies built from generic trusted effect facts.

Every ``rsync`` call is recorded as the ``rsync`` effect with its ``src``,
``dest``, and ``delete`` flag (see ``effects.py``). These contracts let an
agent state where a sync may write, whether it may delete extraneous
destination files, and how many syncs may occur.

``only_dest_under`` is the headline blast-radius guard: it pins every
destination under a fixed prefix, so a sync cannot scribble over an arbitrary
location. ``no_delete`` is the headline data-loss guard: it forbids the
``--delete`` flag, so a sync can only add and update, never prune.
"""

from tools.rsync.trusted.effects import rsync

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Rsync = effect(rsync)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about rsync effects."""
    return core_no_guarantees()


@contract
def no_syncs() -> ContractSpec:
    """Guarantee the program performs no rsync calls."""
    return Rsync.empty()


@contract
def only_dest_under(root: str) -> ContractSpec:
    """Guarantee every sync writes to a destination under ``root``."""
    return Rsync.all(lambda e: e.dest.startswith(root))


@contract
def never_dest_under(prefix: str) -> ContractSpec:
    """Guarantee no sync writes to a destination under ``prefix``."""
    return Rsync.all(lambda e: not e.dest.startswith(prefix))


@contract
def only_src_under(root: str) -> ContractSpec:
    """Guarantee every sync reads from a source under ``root``."""
    return Rsync.all(lambda e: e.src.startswith(root))


@contract
def no_delete() -> ContractSpec:
    """Guarantee no sync passes ``--delete`` (so none can prune the destination)."""
    return Rsync.all(lambda e: not e.delete)


@contract
def syncs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` rsync calls occur."""
    return Rsync.count() <= count
