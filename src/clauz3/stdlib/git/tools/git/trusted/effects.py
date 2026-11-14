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


def is_builtin_subcommand(subcommand: str) -> bool:
    """Whether the general ``git`` effect may run ``subcommand``.

    The list is the main commands of the git version this library targets
    (``git --list-cmds=main`` for git 2.37), minus every command that can
    push or publish (``push``, ``send-pack``, ``http-push``, ``p4``,
    ``subtree``), the ``remote-*`` transport helpers, server-side and daemon
    commands, credential helpers, and internal ``--helper`` commands. Aliases
    and third-party ``git-*`` commands are refused because their meaning
    comes from local configuration: an alias ``p = push`` would otherwise
    push past ``no_push()``. Pushes go through ``git_push``. The list is
    written inline because preconditions cannot read module-level constants.
    """
    return subcommand in [
        "add",
        "am",
        "annotate",
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
        "difftool",
        "fast-export",
        "fast-import",
        "fetch",
        "fetch-pack",
        "filter-branch",
        "fmt-merge-msg",
        "for-each-ref",
        "for-each-repo",
        "format-patch",
        "fsck",
        "fsck-objects",
        "gc",
        "get-tar-commit-id",
        "grep",
        "hash-object",
        "help",
        "hook",
        "http-fetch",
        "imap-send",
        "index-pack",
        "init",
        "init-db",
        "interpret-trailers",
        "log",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "mailinfo",
        "mailsplit",
        "maintenance",
        "merge",
        "merge-base",
        "merge-file",
        "merge-index",
        "merge-octopus",
        "merge-one-file",
        "merge-ours",
        "merge-recursive",
        "merge-recursive-ours",
        "merge-recursive-theirs",
        "merge-resolve",
        "merge-subtree",
        "merge-tree",
        "mergetool",
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
        "pickaxe",
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
        "send-email",
        "shortlog",
        "show",
        "show-branch",
        "show-index",
        "show-ref",
        "sparse-checkout",
        "stage",
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

    ``subcommand`` must pass ``is_builtin_subcommand`` (a precondition), so
    aliases, third-party commands and every pushing command are refused:
    pushes go through ``git_push``, where the push contracts cover them. Any argument
    starting with ``--output`` raises ``ValueError`` before git runs, because
    ``diff``, ``log`` and ``show`` would write their output to that file.
    """
    for arg in args:
        if arg.startswith("--output"):
            raise ValueError(f"git argument {arg!r} is refused: --output writes a file")
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", cwd, subcommand, *args],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout


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
    pin the destination an agent is allowed to publish to.
    """
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", cwd, "push", remote, branch],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout
