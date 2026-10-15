"""coreutils policies built from generic trusted effect facts.

Each bash primitive has its own relation: ``remove``, ``move`` / ``copy`` for
the two-path operations, ``chmod`` for permission changes, and ``make_dir`` /
``touch`` together as the creating pair.

The headline guards are the destructive ones: ``only_delete_under`` bounds
where a program may delete (so an agent cannot ``rm`` outside its sandbox), and
``no_deletes`` forbids deletion entirely. ``move`` and ``copy`` carry both
``src`` and ``dest`` fields, so their destinations can be pinned independently
of their sources.

``make_dir`` / ``touch`` used to be governed by ``filesystem.only_write_under``
and friends, because every one of those effects declared a ``write`` marker and
that contract matched on the string. An effect is addressed by name now (see
:mod:`clauz3.effects`), so this layer states its own creation policy and no
longer depends on which other layers happen to be installed.
"""

from tools.coreutils.trusted.effects import chmod, copy, make_dir, move, remove, touch

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Delete = effect(remove)
Move = effect(move)
Copy = effect(copy)
Chmod = effect(chmod)
Create = effect(make_dir, touch)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about coreutils effects."""
    return core_no_guarantees()


# -- delete (rm) ------------------------------------------------------------


@contract
def no_deletes() -> ContractSpec:
    """Guarantee the program deletes nothing."""
    return Delete.empty()


@contract
def only_delete_under(root: str) -> ContractSpec:
    """Guarantee every deleted path is under ``root``."""
    return Delete.all(lambda e: e.path.startswith(root))


@contract
def never_delete_under(prefix: str) -> ContractSpec:
    """Guarantee no deleted path is under ``prefix``.

    The blast-radius guard for handing an agent ``rm``: e.g.
    ``never_delete_under("/etc")`` or ``never_delete_under("/home/user/.ssh")``.
    """
    return Delete.all(lambda e: not e.path.startswith(prefix))


@contract
def deletes_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` deletions occur."""
    return Delete.count() <= count


# -- move (mv) --------------------------------------------------------------


@contract
def no_moves() -> ContractSpec:
    """Guarantee the program moves nothing."""
    return Move.empty()


@contract
def only_move_under(root: str) -> ContractSpec:
    """Guarantee every move's destination is under ``root``."""
    return Move.all(lambda e: e.dest.startswith(root))


@contract
def never_move_from_under(prefix: str) -> ContractSpec:
    """Guarantee no move reads its source from under ``prefix``."""
    return Move.all(lambda e: not e.src.startswith(prefix))


# -- copy (cp) --------------------------------------------------------------


@contract
def no_copies() -> ContractSpec:
    """Guarantee the program copies nothing."""
    return Copy.empty()


@contract
def only_copy_under(root: str) -> ContractSpec:
    """Guarantee every copy's destination is under ``root``."""
    return Copy.all(lambda e: e.dest.startswith(root))


@contract
def never_copy_from_under(prefix: str) -> ContractSpec:
    """Guarantee no copy reads its source from under ``prefix``.

    An exfiltration guard: e.g. ``never_copy_from_under("/home/user/.ssh")``
    stops an agent staging private keys for later upload.
    """
    return Copy.all(lambda e: not e.src.startswith(prefix))


# -- chmod ------------------------------------------------------------------


@contract
def no_chmods() -> ContractSpec:
    """Guarantee the program changes no permission bits."""
    return Chmod.empty()


@contract
def only_modes(allowlist: list[int]) -> ContractSpec:
    """Guarantee every chmod sets a mode in ``allowlist``.

    Bitwise masking is not expressible in a contract lambda, so permission
    safety is stated as an explicit allowlist of whole modes, e.g.
    ``only_modes([0o644, 0o755, 0o600, 0o700])`` — which by construction admits
    no world-writable, setuid, or setgid bits.
    """
    return Chmod.all(lambda e: e.mode in allowlist)


@contract
def only_chmod_under(root: str) -> ContractSpec:
    """Guarantee every chmod targets a path under ``root``."""
    return Chmod.all(lambda e: e.path.startswith(root))


# -- create (mkdir / touch) -------------------------------------------------


@contract
def no_creates() -> ContractSpec:
    """Guarantee the program creates no directories or empty files."""
    return Create.empty()


@contract
def only_create_under(root: str) -> ContractSpec:
    """Guarantee every created directory or file is under ``root``."""
    return Create.all(lambda e: e.path.startswith(root))


@contract
def never_create_under(prefix: str) -> ContractSpec:
    """Guarantee nothing is created under ``prefix``."""
    return Create.all(lambda e: not e.path.startswith(prefix))


@contract
def creates_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` directories or empty files are created."""
    return Create.count() <= count
