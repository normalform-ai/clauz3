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
#: The ``read_only`` description names these literally; keep them in sync.
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


@contract(description="No restrictions are imposed on git commands.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about git commands."""
    return core_no_guarantees()


@contract(
    description=(
        "No git commands are run. The separate git push tool has its own guarantees."
    )
)
def no_git() -> ContractSpec:
    """Guarantee the program runs no general git commands.

    Covers the ``git`` effect only and does not by itself forbid
    ``git_push``; stack ``no_push()`` for that.
    """
    return Git.empty()


@contract(
    description=(
        "Git commands are limited to status, log, diff, show, branch, remote, "
        "rev-parse, ls-files, ls-tree, blame, describe, tag, shortlog, reflog, "
        "cat-file and config, and the separate git push tool is not covered. "
        "Some of these can still change the local repository depending on "
        "their options, for example creating a branch or tag or changing a "
        "setting."
    )
)
def read_only() -> ContractSpec:
    """Guarantee git commands are limited to a fixed list of inspection commands.

    Pins each subcommand of the ``git`` effect to ``READ_ONLY_SUBCOMMANDS``.
    Arguments are not constrained, so several listed subcommands can still
    write locally: ``branch``/``tag`` can create or delete refs, ``remote``
    and ``config`` can change settings, ``reflog`` can expire entries, and
    ``diff``/``log`` accept ``--output=<file>``. ``git_push`` is a separate
    effect; stack ``no_push()`` to forbid it.
    """
    return Git.all(lambda e: e.subcommand in READ_ONLY_SUBCOMMANDS)


@contract(
    description=(
        "Git commands are limited to: {allowlist}. The separate git push tool "
        "has its own guarantees."
    )
)
def only_subcommands(allowlist: list[str]) -> ContractSpec:
    """Guarantee every git command is one of the listed commands.

    Compares the subcommand of each ``git`` effect against ``allowlist``;
    ``git_push`` is not covered.
    """
    return Git.all(lambda e: e.subcommand in allowlist)


@contract(
    description=(
        "None of these git commands are run: {blocklist}. The separate git push "
        "tool has its own guarantees."
    )
)
def never_subcommands(blocklist: list[str]) -> ContractSpec:
    """Guarantee none of the listed git commands is run.

    Useful to veto specific destructive subcommands (``reset``, ``clean``,
    ``rebase``) while leaving the rest of git available. ``git_push`` is not
    covered.
    """
    return Git.all(lambda e: e.subcommand not in blocklist)


@contract(
    description=(
        "Git commands run only in folders whose path starts with {root}. The "
        "separate git push tool is not covered."
    )
)
def only_in(root: str) -> ContractSpec:
    """Guarantee every git command runs in a folder whose path starts with root.

    A literal prefix check on ``cwd``, not resolved directory containment
    (``/repo`` also admits ``/repo-other``). Arguments that name other paths
    are not checked, and ``git_push`` is not covered.
    """
    return Git.all(lambda e: e.cwd.startswith(root))


@contract(
    description=(
        "At most {count} git commands are run, not counting the separate git push tool."
    )
)
def runs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` general git commands run."""
    return Git.count() <= count


@contract(
    description=(
        "Nothing is pushed with the git push tool. A push run as a general git "
        "command is not covered."
    )
)
def no_push() -> ContractSpec:
    """Guarantee the program never uses the git push tool.

    Forbids the ``git_push`` effect only. ``git("push", ...)`` is an ordinary
    ``git`` effect; forbid it with ``read_only()`` or
    ``never_subcommands(["push"])``.
    """
    return Push.empty()


@contract(
    description=(
        "Anything pushed with the git push tool goes only to the remote "
        "{remote}. A push run as a general git command is not covered."
    )
)
def only_push_to_remote(remote: str) -> ContractSpec:
    """Guarantee every use of the git push tool targets the remote ``remote``.

    Covers ``git_push`` only, not ``git("push", ...)``. The branch argument is
    not constrained.
    """
    return Push.all(lambda e: e.remote == remote)


@contract(
    description=(
        "Anything pushed with the git push tool goes only to the branch "
        "{branch}. A push run as a general git command is not covered."
    )
)
def only_push_to_branch(branch: str) -> ContractSpec:
    """Guarantee every use of the git push tool pushes exactly ``branch``.

    Covers ``git_push`` only, not ``git("push", ...)``.
    """
    return Push.all(lambda e: e.branch == branch)
