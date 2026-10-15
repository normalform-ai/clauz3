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
    """
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
