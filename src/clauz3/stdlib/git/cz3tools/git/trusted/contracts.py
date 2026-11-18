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

from cz3tools.git.trusted.effects import git, git_push

from clauz3.spec import ConjunctionSpec, ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Git = effect(git)
Push = effect(git_push)

#: Subcommands that only read repository state — they touch neither the working
#: tree, the index, nor any remote. ``read_only`` pins every subcommand to this
#: set. Deliberately excludes network commands like ``fetch``/``pull``, any
#: mutation (``commit``, ``checkout``, ``reset``, ``clean``, ``rebase``, ...),
#: and commands that write depending on their options (``branch``, ``tag``,
#: ``remote``, ``config``, ``reflog``).
#: The ``read_only`` description names these literally; keep them in sync.
READ_ONLY_SUBCOMMANDS = [
    "status",
    "log",
    "diff",
    "show",
    "rev-parse",
    "ls-files",
    "ls-tree",
    "blame",
    "describe",
    "shortlog",
    "cat-file",
]


@contract(description="No restrictions are imposed on git commands.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about git commands."""
    return core_no_guarantees()


@contract(description="No git commands are run; pushes are covered separately.")
def no_git() -> ContractSpec:
    """Guarantee the program runs no git commands other than pushes.

    Covers the ``git`` effect only and does not by itself forbid
    ``git_push``; stack ``no_push()`` for that.
    """
    return Git.empty()


@contract(
    description=(
        "Git commands are limited to inspection commands (status, log, diff, "
        "show, rev-parse, ls-files, ls-tree, blame, describe, shortlog, "
        "cat-file); nothing is committed, no history is changed and nothing is "
        "pushed, though these commands may read files outside the repository. "
        "Programs named in git settings (the repository's or your own), such as "
        "a diff tool, can still run."
    )
)
def read_only() -> ContractSpec:
    """Guarantee git commands only look at the repository and nothing is pushed.

    Pins each subcommand of the ``git`` effect to ``READ_ONLY_SUBCOMMANDS``.
    None of these writes the working tree, refs or settings (``status`` may
    refresh the index's file-stat cache); the ``git`` effect refuses
    ``--output``, the one option among them that writes a file. ``diff
    --no-index`` and ``blame --contents`` can read files outside the
    repository. Pushes use the separate ``git_push`` effect, so this also
    stacks ``no_push()``.
    """
    return ConjunctionSpec(
        (Git.all(lambda e: e.subcommand in READ_ONLY_SUBCOMMANDS), Push.empty())
    )


@contract(
    description=(
        "Git commands are limited to: {allowlist}; pushes are covered separately."
    )
)
def only_subcommands(allowlist: list[str]) -> ContractSpec:
    """Guarantee every git command is one of the listed commands.

    Compares the subcommand of each ``git`` effect against ``allowlist``.
    Pushes use ``git_push`` and are governed by the push contracts.
    """
    return Git.all(lambda e: e.subcommand in allowlist)


@contract(
    description=(
        "None of these git commands are run: {blocklist}; pushes are covered "
        "separately. "
        "A git command told to run another program (for example rebase --exec), or "
        "a program named in git settings (the repository's or your own), is not "
        "covered."
    )
)
def never_subcommands(blocklist: list[str]) -> ContractSpec:
    """Guarantee none of the listed git commands is run.

    Useful to veto specific destructive subcommands (``reset``, ``clean``,
    ``rebase``) while leaving the rest of git available. Pushes use
    ``git_push`` and are governed by the push contracts.
    """
    return Git.all(lambda e: e.subcommand not in blocklist)


@contract(
    description=(
        "Git commands, including pushes, run only in folders whose path starts "
        "with {root}. Other folders named in a command (such as where a clone "
        "or new worktree goes), a git command told to run another program (for "
        "example rebase --exec) and programs named in git settings (the "
        "repository's or your own) are not covered."
    )
)
def only_in(root: str) -> ContractSpec:
    """Guarantee every git command and push runs in a folder starting with root.

    A literal prefix check on ``cwd``, not resolved directory containment
    (``/repo`` also admits ``/repo-other``) on the ``cwd`` of both ``git`` and
    ``git_push``. Arguments that name other paths are not checked.
    """
    return ConjunctionSpec(
        (
            Git.all(lambda e: e.cwd.startswith(root)),
            Push.all(lambda e: e.cwd.startswith(root)),
        )
    )


@contract(description="At most {count} git commands are run, not counting pushes.")
def runs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` git commands run, not counting pushes."""
    return Git.count() <= count


@contract(
    description=(
        "Nothing is pushed with git. "
        "A git command told to run another program (for example rebase --exec), or "
        "a program named in git settings (the repository's or your own), is not "
        "covered."
    )
)
def no_push() -> ContractSpec:
    """Guarantee the program pushes nothing with git.

    Forbids ``git_push``. The general ``git`` effect refuses ``push`` and
    every other pushing command or alias, but commands that can be told to
    run arbitrary programs (``rebase --exec``, ``submodule foreach``,
    ``bisect run``, ``fetch --upload-pack``, ``grep -O``) or a program named
    in git settings, the repository's or the user's (a diff, merge or filter
    driver, ``core.sshCommand``; ``config --global`` writes the user's), can
    still push. Hooks and ``core.fsmonitor`` never
    run: the effects disable them.
    """
    return Push.empty()


@contract(
    description=(
        "Anything pushed with git goes only to the remote {remote}. "
        "A git command told to run another program (for example rebase --exec), or "
        "a program named in git settings (the repository's or your own), is not "
        "covered."
    )
)
def only_push_to_remote(remote: str) -> ContractSpec:
    """Guarantee every git push targets the remote ``remote``.

    The branch argument is not constrained. As with ``no_push``, commands
    that run arbitrary programs are not covered.
    """
    return Push.all(lambda e: e.remote == remote)


@contract(
    description=(
        "Anything pushed with git goes only to the branch {branch}. "
        "A git command told to run another program (for example rebase --exec), or "
        "a program named in git settings (the repository's or your own), is not "
        "covered."
    )
)
def only_push_to_branch(branch: str) -> ContractSpec:
    """Guarantee every git push pushes exactly ``branch``.

    As with ``no_push``, commands that run arbitrary programs are not covered.
    """
    return Push.all(lambda e: e.branch == branch)
