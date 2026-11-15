"""Trusted git effects: real (non-mock) local ``git`` subprocesses.

The workhorse ``git`` effect runs any git subcommand in a working directory and
returns its stdout. It is recorded by name so the contracts in
``contracts.py`` can scope *which* subcommands an agent may run (the headline
``read_only`` guard), *where* they run, and *how many* times.

``git_push`` is split out as its own effect: pushing is the one
local-git operation that mutates a remote and touches the network, so its
remote and branch target deserve their own policy surface rather than being
folded into an opaque argument list.

The bodies shell out with an explicit argument vector (never a shell string),
so a subcommand and its arguments cannot smuggle extra commands. Each body uses
``subprocess.run``, so ``syscall`` is declared among their deal markers. The
prover never runs these bodies — it records each call as a fact and proves
the declared contracts. The bodies run only under ``clauz3 run``.
"""

import subprocess

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments

DEFAULT_TIMEOUT_SECONDS = 120

#: Prepended to every git command line. Allowed commands can write files into
#: ``.git/hooks`` (``checkout-index --prefix=.git/``), and a later ``commit``
#: would run such a hook, so hooks are disabled for every call by pointing
#: ``core.hooksPath`` at a path that contains none.
NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]


def is_builtin_subcommand(subcommand: str) -> bool:
    """Whether the general ``git`` effect may run ``subcommand``.

    The list is the main commands of the git version this library targets
    (``git --list-cmds=main`` for git 2.37), minus:

    - every command that can push or publish (``push``, ``send-pack``,
      ``http-push``, ``p4``, ``subtree``, ``send-email``, ``imap-send``);
    - commands whose main purpose is running other programs or other git
      commands (``for-each-repo``, ``filter-branch``, ``difftool``,
      ``mergetool``, ``merge-index``, ``hook``, ``maintenance``, which also
      registers a system scheduler);
    - synonyms of other commands (``stage`` = ``add``, ``annotate`` and
      ``pickaxe`` = ``blame``, ``init-db`` = ``init``, ``fsck-objects`` =
      ``fsck``), so ``never_subcommands`` cannot be bypassed by a second name;
    - the ``remote-*`` transport helpers, server-side and daemon commands,
      credential helpers, and internal ``--helper`` commands.

    Aliases and third-party ``git-*`` commands are refused because their
    meaning comes from local configuration: an alias ``p = push`` would
    otherwise push past ``no_push()``. Pushes go through ``git_push``.
    Commands that can still be told to run another program (``rebase
    --exec``, ``submodule foreach``, ``bisect run``, ``fetch
    --upload-pack``, ``grep -O``/``--open-files-in-pager``, or settings
    that name a program, such as ``core.fsmonitor`` or a filter driver)
    remain; contract descriptions say they are not covered. Hooks never run:
    see ``NO_HOOKS``. The list is written inline
    because preconditions cannot read module-level constants.
    """
    return subcommand in [
        "add",
        "am",
        "apply",
        "archive",
        "bisect",
        "blame",
        "branch",
        "bugreport",
        "bundle",
        "cat-file",
        "check-attr",
        "check-ignore",
        "check-mailmap",
        "check-ref-format",
        "checkout",
        "checkout-index",
        "cherry",
        "cherry-pick",
        "clean",
        "clone",
        "column",
        "commit",
        "commit-graph",
        "commit-tree",
        "config",
        "count-objects",
        "describe",
        "diff",
        "diff-files",
        "diff-index",
        "diff-tree",
        "fast-export",
        "fast-import",
        "fetch",
        "fetch-pack",
        "fmt-merge-msg",
        "for-each-ref",
        "format-patch",
        "fsck",
        "gc",
        "get-tar-commit-id",
        "grep",
        "hash-object",
        "help",
        "http-fetch",
        "index-pack",
        "init",
        "interpret-trailers",
        "log",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "mailinfo",
        "mailsplit",
        "merge",
        "merge-base",
        "merge-file",
        "merge-octopus",
        "merge-one-file",
        "merge-ours",
        "merge-recursive",
        "merge-recursive-ours",
        "merge-recursive-theirs",
        "merge-resolve",
        "merge-subtree",
        "merge-tree",
        "mktag",
        "mktree",
        "multi-pack-index",
        "mv",
        "name-rev",
        "notes",
        "pack-objects",
        "pack-redundant",
        "pack-refs",
        "patch-id",
        "prune",
        "prune-packed",
        "pull",
        "quiltimport",
        "range-diff",
        "read-tree",
        "rebase",
        "reflog",
        "remote",
        "repack",
        "replace",
        "request-pull",
        "rerere",
        "reset",
        "restore",
        "rev-list",
        "rev-parse",
        "revert",
        "rm",
        "shortlog",
        "show",
        "show-branch",
        "show-index",
        "show-ref",
        "sparse-checkout",
        "stash",
        "status",
        "stripspace",
        "submodule",
        "switch",
        "symbolic-ref",
        "tag",
        "unpack-file",
        "unpack-objects",
        "update-index",
        "update-ref",
        "update-server-info",
        "var",
        "verify-commit",
        "verify-pack",
        "verify-tag",
        "version",
        "whatchanged",
        "worktree",
        "write-tree",
    ]


@clauz3.requires(
    lambda subcommand, args, cwd: is_builtin_subcommand(subcommand),
    message="subcommand must be a built-in git command; push with git_push",
)
@clauz3.requires(
    lambda subcommand, args, cwd: has_no_parent_segments(cwd),
    message="cwd must not contain parent-directory segments",
)
@clauz3.requires(
    lambda subcommand, args, cwd: len(subcommand) > 0 and len(cwd) > 0,
    message="subcommand and cwd must be non-empty",
)
@deal.has("io", "syscall")
@clauz3.trusted
def git(subcommand: str, args: list[str], cwd: str) -> str:
    """Run ``git <subcommand> <args...>`` in ``cwd`` and return its stdout.

    Arguments are passed as an explicit vector, so neither ``subcommand`` nor
    any entry of ``args`` is interpreted by a shell. Which subcommands may run
    (e.g. ``read_only``) and where they may run (``only_in``) are agent-stated
    guarantees over the ``git`` effect.

    ``subcommand`` must pass ``is_builtin_subcommand``, so aliases,
    third-party commands, synonyms, every pushing command and commands built
    to run other programs are refused: pushes go through ``git_push``, where
    the push contracts cover them. This and the other preconditions are
    checked again in the body, because Deal's runtime checks are optional.

    Any argument starting with ``--output`` raises ``ValueError``, because
    ``diff``, ``log`` and ``show`` would write their output to that file. The
    prover cannot check this, so it is refused only when the program runs,
    possibly after earlier calls.
    """
    if not (len(subcommand) > 0 and len(cwd) > 0):
        raise ValueError("subcommand and cwd must be non-empty")
    if not has_no_parent_segments(cwd):
        raise ValueError("cwd must not contain parent-directory segments")
    if not is_builtin_subcommand(subcommand):
        raise ValueError(
            f"git subcommand {subcommand!r} is not an allowed built-in git "
            "command; push with git_push"
        )
    for arg in args:
        if arg.startswith("--output"):
            raise ValueError(f"git argument {arg!r} is refused: --output writes a file")
    completed = subprocess.run(  # noqa: S603
        ["git", *NO_HOOKS, "-C", cwd, subcommand, *args],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout


@clauz3.requires(
    lambda remote, branch, cwd: (
        not remote.startswith("-") and not branch.startswith("-")
    ),
    message="remote and branch must not start with '-' (they would be options)",
)
@clauz3.requires(
    lambda remote, branch, cwd: has_no_parent_segments(cwd),
    message="cwd must not contain parent-directory segments",
)
@clauz3.requires(
    lambda remote, branch, cwd: len(remote) > 0 and len(branch) > 0 and len(cwd) > 0,
    message="remote, branch, and cwd must be non-empty",
)
@deal.has("network", "io", "syscall")
@clauz3.trusted
def git_push(remote: str, branch: str, cwd: str) -> str:
    """Push ``branch`` to ``remote`` from the repository at ``cwd``.

    Recorded as its own effect so ``contracts.no_push()`` can forbid
    pushing entirely, while ``only_push_to_remote`` / ``only_push_to_branch``
    pin the destination an agent is allowed to publish to. ``remote`` and
    ``branch`` may not start with ``-``, so neither can become an option such
    as ``--mirror`` or ``--force``. Preconditions are checked again in the
    body, because Deal's runtime checks are optional.
    """
    if not (len(remote) > 0 and len(branch) > 0 and len(cwd) > 0):
        raise ValueError("remote, branch, and cwd must be non-empty")
    if not has_no_parent_segments(cwd):
        raise ValueError("cwd must not contain parent-directory segments")
    if remote.startswith("-") or branch.startswith("-"):
        raise ValueError("remote and branch must not start with '-'")
    completed = subprocess.run(  # noqa: S603
        ["git", *NO_HOOKS, "-C", cwd, "push", remote, branch],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout
