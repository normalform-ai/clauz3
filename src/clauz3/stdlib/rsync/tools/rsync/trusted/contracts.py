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
        "Every rsync copy names a destination starting with {root}; extra "
        "options passed to rsync are not checked."
    )
)
def only_dest_under(root: str) -> ContractSpec:
    """Guarantee every rsync copy names a destination starting with ``root``.

    A literal prefix check on ``dest``. Extra ``args`` are not checked, so an
    option such as ``--backup-dir`` or ``--log-file`` can still write
    elsewhere.
    """
    return Rsync.all(lambda e: e.dest.startswith(root))


@contract(
    description=(
        "No rsync copy names a destination starting with {prefix}; extra "
        "options passed to rsync are not checked."
    )
)
def never_dest_under(prefix: str) -> ContractSpec:
    """Guarantee no rsync copy names a destination starting with ``prefix``.

    A literal prefix check on ``dest``; extra ``args`` are not checked.
    """
    return Rsync.all(lambda e: not e.dest.startswith(prefix))


@contract(
    description=(
        "Every rsync copy names a source starting with {root}; extra options "
        "and extra source paths passed to rsync are not checked."
    )
)
def only_src_under(root: str) -> ContractSpec:
    """Guarantee every rsync copy names a source starting with ``root``.

    A literal prefix check on ``src``. Extra ``args`` precede ``src`` on the
    command line, so a path among them is an additional, unchecked source.
    """
    return Rsync.all(lambda e: e.src.startswith(root))


@contract(
    description=(
        "No rsync copy turns on the tool's delete setting, which removes "
        "destination files missing from the source; delete options passed as "
        "extra rsync options (such as --delete) are not checked."
    )
)
def no_delete() -> ContractSpec:
    """Guarantee no rsync copy turns on the ``delete`` parameter.

    Checks only the ``delete`` flag of each call. Extra ``args`` are not
    checked, so ``--delete``, ``--del`` or ``--remove-source-files`` passed
    there still delete files.
    """
    return Rsync.all(lambda e: not e.delete)


@contract(description="At most {count} rsync file copies are run.")
def syncs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` rsync file copies are run."""
    return Rsync.count() <= count
