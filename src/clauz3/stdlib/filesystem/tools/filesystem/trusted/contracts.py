"""Filesystem path policies built from generic trusted effect facts.

These contracts let an agent state *where* it is allowed to read and write, as
path-prefix policies over this layer's effects. The prefix checks use the
relation-lambda ``str.startswith`` support in ``clauz3.spec``.

The ``reads_under`` / ``never_reads_under`` builders take the relation to
constrain, so a layer whose effect is *also* a read --- ``grep``, ``find`` ---
can apply this layer's path rule to its own effect. That delegation used to be
implicit: every read effect declared a ``read`` marker and ``Read`` matched
whatever carried it, including effects from layers this one has never heard
of. It is a line of code in the dependent layer now, so which effects a policy
governs is something the contract states rather than something a string
collision decides.
"""

from tools.filesystem.trusted.effects import read_file, read_lines, write_file

from clauz3.spec import ContractSpec, EffectRelation, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Read = effect(read_file, read_lines)
Write = effect(write_file)


def reads_under(relation: EffectRelation, root: str) -> ContractSpec:
    """Every call in ``relation`` touches a path under ``root``."""
    return relation.all(lambda e: e.path.startswith(root))


def never_reads_under(relation: EffectRelation, prefix: str) -> ContractSpec:
    """No call in ``relation`` touches a path under ``prefix``."""
    return relation.all(lambda e: not e.path.startswith(prefix))


@contract(description="No restrictions are imposed on filesystem effects.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about filesystem effects."""
    return core_no_guarantees()


@contract(description="No calls to filesystem.write_file are permitted.")
def read_only() -> ContractSpec:
    """Guarantee ``write_file`` is never called.

    This layer's write effect, and only that one. A program can still write
    through another layer --- ``coreutils.make_dir``, ``editor.edit_file``,
    ``rsync.rsync`` --- and each states its own: ``coreutils.no_creates()``,
    ``editor.no_edits()``, ``rsync.no_syncs()``.
    """
    return Write.empty()


@contract(
    description=(
        "No calls to filesystem.read_file or filesystem.read_lines are permitted."
    )
)
def no_reads() -> ContractSpec:
    """Guarantee neither ``read_file`` nor ``read_lines`` is called.

    This layer's read effects only. Reading through another
    layer is that layer's to state: ``grep.searches_at_most(0)``,
    ``find.searches_at_most(0)``, ``db.no_db_reads()``, ``env.no_env_reads()``.
    """
    return Read.empty()


@contract(
    description=(
        "Every filesystem.read_file or filesystem.read_lines "
        "path must start with {root}."
    )
)
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every ``read_file``/``read_lines`` path starts with ``root``.

    This is a literal string-prefix check, not resolved directory containment
    or an exact-file check. For example, ``test.md`` also matches ``test.md.bak``.

    A read through another layer is that layer's to state:
    ``grep.only_read_under``, ``find.only_read_under``, ``db.only_read_under``.
    """
    return reads_under(Read, root)


@contract(description="Every filesystem.write_file path must start with {root}.")
def only_write_under(root: str) -> ContractSpec:
    """Guarantee every ``write_file`` call has a path under ``root``.

    A write through another layer is that layer's to state:
    ``coreutils.only_create_under``, ``editor.only_edit_under``,
    ``rsync.only_dest_under``.
    """
    return Write.all(lambda e: e.path.startswith(root))


@contract(
    description=(
        "No filesystem.read_file or filesystem.read_lines path may start with {prefix}."
    )
)
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no ``read_file``/``read_lines`` path is under ``prefix``.

    A read through another layer is that layer's to state:
    ``grep.never_read_under``, ``find.never_read_under``,
    ``db.never_read_under``.
    """
    return never_reads_under(Read, prefix)


@contract(description="No filesystem.write_file path may start with {prefix}.")
def never_write_under(prefix: str) -> ContractSpec:
    """Guarantee no ``write_file`` call has a path under ``prefix``.

    A write through another layer is that layer's to state:
    ``coreutils.never_create_under``, ``editor.never_edit_under``,
    ``rsync.never_dest_under``.
    """
    return Write.all(lambda e: not e.path.startswith(prefix))


@contract(description="Calls to filesystem.write_file are limited to {count} in total.")
def writes_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` ``write_file`` calls occur.

    Other layers count their own: ``coreutils.creates_at_most``,
    ``editor.edits_at_most``, ``rsync.syncs_at_most``.
    """
    return Write.count() <= count
