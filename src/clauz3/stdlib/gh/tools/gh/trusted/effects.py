"""Trusted GitHub-CLI effect: a real (non-mock) ``gh`` subprocess.

``gh`` runs a single GitHub CLI invocation of the shape
``gh <command> <action> <args...> --repo <repo>`` and returns its stdout. It
is recorded by name so the contracts in ``contracts.py`` can scope which
GitHub objects an agent may touch (``command``, e.g. ``pr`` / ``issue``),
which verbs it may run (``action``, e.g. ``view`` / ``merge``), and which
repository it may target (``repo``).

Splitting ``command`` and ``action`` is the genuinely useful policy surface a
raw shell allowlist cannot express statically: ``read_only`` constrains the
*verb* (no ``merge`` / ``close`` / ``delete``) while ``only_commands`` and
``only_repo`` independently bound the *noun* and the *repository*.

The body shells out via ``subprocess.run`` with an explicit argument vector
(never a shell string), so ``syscall`` is declared among its deal markers. The
prover never runs the body — it records each call as a fact and proves the
contract; the body runs only under ``clauz3 run``.
"""

import subprocess

import deal

import clauz3

DEFAULT_TIMEOUT_SECONDS = 120


@clauz3.requires(
    lambda command, action, args, repo: (
        len(command) > 0 and len(action) > 0 and len(repo) > 0
    ),
    message="command, action, and repo must be non-empty",
)
@deal.has("network", "io", "syscall")
@clauz3.trusted
def gh(command: str, action: str, args: list[str], repo: str) -> str:
    """Run ``gh <command> <action> <args...> --repo <repo>`` and return stdout.

    Arguments are passed as an explicit vector, so no field is interpreted by a
    shell. ``repo`` is always supplied as ``--repo`` so every call is scoped to
    a single repository, which ``only_repo`` / ``only_owner`` can then pin.
    """
    completed = subprocess.run(  # noqa: S603
        ["gh", command, action, *args, "--repo", repo],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout
