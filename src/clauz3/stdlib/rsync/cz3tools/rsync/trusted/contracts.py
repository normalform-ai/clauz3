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

from cz3tools.rsync.trusted.effects import rsync

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Rsync = effect(rsync)


@contract(description="No restrictions are imposed on rsync file copies.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about rsync file copies."""
    return core_no_guarantees()


@contract(description="No rsync file copies are run.")
def no_syncs() -> ContractSpec:
    """Guarantee the program runs no rsync file copies."""
    return Rsync.empty()


@contract(
    description=(
        "Every rsync copy writes only to a destination whose path starts with {root}."
    )
)
def only_dest_under(root: str) -> ContractSpec:
    """Guarantee every rsync copy writes only under a destination starting with root.

    A literal prefix check on ``dest``. The ``rsync`` effect refuses options
    that write elsewhere (``--backup-dir``, ``--log-file``, ``--temp-dir``,
    ...), so ``dest`` is the only place a copy writes.
    """
    return Rsync.all(lambda e: e.dest.startswith(root))


@contract(
    description=(
        "No rsync copy writes to a destination whose path starts with {prefix}."
    )
)
def never_dest_under(prefix: str) -> ContractSpec:
    """Guarantee no rsync copy writes to a destination starting with ``prefix``.

    A literal prefix check on ``dest``; the ``rsync`` effect refuses options
    that write elsewhere.
    """
    return Rsync.all(lambda e: not e.dest.startswith(prefix))


@contract(
    description=(
        "Every rsync copy names a source starting with {root}; extra source "
        "paths given among the other rsync options are not checked, and "
        "following links (for example -L or -k) can read outside it."
    )
)
def only_src_under(root: str) -> ContractSpec:
    """Guarantee every rsync copy names a source starting with ``root``.

    A literal prefix check on ``src``. The ``rsync`` effect refuses options
    that read from elsewhere (``--files-from``, ``--link-dest``, ...), but a
    plain path in ``args`` precedes ``src`` on the command line and is an
    additional, unchecked source. ``-L``/``--copy-links``,
    ``--copy-unsafe-links`` and ``-k``/``--copy-dirlinks`` follow symlinks in
    the source, so they can read files outside ``root``. They are disclosed
    rather than refused because following links is an ordinary way to copy a
    tree.
    """
    return Rsync.all(lambda e: e.src.startswith(root))


@contract(
    description=(
        "No rsync copy deletes files; existing destination files can still be "
        "overwritten."
    )
)
def no_delete() -> ContractSpec:
    """Guarantee no rsync copy deletes files.

    Checks the ``delete`` parameter of each call; the ``rsync`` effect refuses
    every deleting option in ``args`` (``--delete*``, ``--del``,
    ``--remove-source-files``).
    """
    return Rsync.all(lambda e: not e.delete)


@contract(description="At most {count} rsync file copies are run.")
def syncs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` rsync file copies are run."""
    return Rsync.count() <= count
