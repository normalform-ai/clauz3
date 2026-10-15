"""Git subcommand and push policies built from generic trusted effect facts.

Local git subcommands are recorded as the ``git`` effect and pushes as
``git_push`` (see ``effects.py``). These contracts let an agent state which
subcommands it may run, where they run, how many run, and — for pushes — which
remote and branch it is allowed to publish to.

``read_only`` is the headline guard: it pins every subcommand to a fixed set of
inspection commands that cannot mutate the working tree, the index, or any
remote, which is exactly what you want when handing a repository to an agent
that should only look.
"""

from tools.git.trusted.effects import git, git_push

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Git = effect(git)
Push = effect(git_push)

#: Subcommands that only read repository state — they touch neither the working
#: tree, the index, nor any remote. ``read_only`` pins every subcommand to this
#: set. Deliberately excludes network commands like ``fetch``/``pull`` and any
#: mutation (``commit``, ``checkout``, ``reset``, ``clean``, ``rebase``, ...).
READ_ONLY_SUBCOMMANDS = [
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "remote",
    "rev-parse",
    "ls-files",
    "ls-tree",
    "blame",
    "describe",
    "tag",
    "shortlog",
    "reflog",
    "cat-file",
    "config",
]


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about git effects."""
    return core_no_guarantees()


@contract
def no_git() -> ContractSpec:
    """Guarantee the program runs no local git subcommands.

    Does not by itself forbid ``git_push``; stack ``no_push()`` for that.
    """
    return Git.empty()


@contract
def read_only() -> ContractSpec:
    """Guarantee every git subcommand is a read-only inspection command.

    Pins each subcommand to ``READ_ONLY_SUBCOMMANDS``, so the program cannot
    mutate the working tree, index, or any remote via the ``git`` effect.
    """
    return Git.all(lambda e: e.subcommand in READ_ONLY_SUBCOMMANDS)


@contract
def only_subcommands(allowlist: list[str]) -> ContractSpec:
    """Guarantee every git subcommand is in ``allowlist``."""
    return Git.all(lambda e: e.subcommand in allowlist)


@contract
def never_subcommands(blocklist: list[str]) -> ContractSpec:
    """Guarantee no git subcommand is in ``blocklist``.

    Useful to veto specific destructive subcommands (``reset``, ``clean``,
    ``rebase``) while leaving the rest of git available.
    """
    return Git.all(lambda e: e.subcommand not in blocklist)


@contract
def only_in(root: str) -> ContractSpec:
    """Guarantee every git subcommand runs in a directory under ``root``."""
    return Git.all(lambda e: e.cwd.startswith(root))


@contract
def runs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` git subcommands run."""
    return Git.count() <= count


@contract
def no_push() -> ContractSpec:
    """Guarantee the program performs no git pushes."""
    return Push.empty()


@contract
def only_push_to_remote(remote: str) -> ContractSpec:
    """Guarantee every push targets the remote named ``remote``."""
    return Push.all(lambda e: e.remote == remote)


@contract
def only_push_to_branch(branch: str) -> ContractSpec:
    """Guarantee every push targets the branch named ``branch``."""
    return Push.all(lambda e: e.branch == branch)
