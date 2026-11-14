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


def is_builtin_command(command: str) -> bool:
    """Whether ``command`` is one of gh's built-in top-level commands.

    The list is the built-in command set of the gh version this library
    targets (gh 2.101: the core, GitHub Actions and additional command groups
    of ``gh --help``). ``alias`` and ``extension`` are excluded, as is
    ``copilot``, which runs a separately installed program. Aliases and
    extensions are refused because their meaning comes from local
    configuration, so a contract over the second word could not describe
    what they do. The list is written inline because preconditions cannot
    read module-level constants.
    """
    return command in [
        "auth",
        "browse",
        "codespace",
        "discussion",
        "gist",
        "issue",
        "org",
        "pr",
        "project",
        "release",
        "repo",
        "skill",
        "cache",
        "run",
        "workflow",
        "agent-task",
        "api",
        "attestation",
        "completion",
        "config",
        "gpg-key",
        "label",
        "licenses",
        "preview",
        "ruleset",
        "search",
        "secret",
        "ssh-key",
        "status",
        "variable",
    ]


@clauz3.requires(
    lambda command, action, args, repo: (
        not (
            (command == "issue" and action == "transfer")
            or (command == "label" and action == "clone")
        )
    ),
    message="issue transfer and label clone reach a second repository",
)
@clauz3.requires(
    lambda command, action, args, repo: is_builtin_command(command),
    message="command must be a built-in gh command (no aliases or extensions)",
)
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

    ``command`` must be one of gh's built-in commands (``is_builtin_command``,
    a precondition), so aliases and extensions are refused. ``issue transfer``
    and ``label clone`` are refused too (a precondition): they take a second
    repository as an ordinary argument, past ``--repo``.

    Refer to pull requests and issues by number, not by web address: gh
    resolves a URL against the URL's own repository, ignoring ``--repo``. Any
    argument containing ``://`` or ``github.com`` (in any letter case) raises
    ``ValueError`` before gh runs, so ``only_repo`` / ``only_owner`` hold.
    Put text that must contain a link in a file and pass ``--body-file``.

    Long flags must be passed as separate arguments (``["--body", "text"]``):
    any argument that starts with ``--`` and contains ``=`` raises
    ``ValueError`` before gh runs. This keeps one spelling per flag, so a
    contract such as ``no_admin_override`` can check for ``"--admin"`` by
    exact membership without ``--admin=true`` slipping past it.
    """
    for arg in args:
        if "://" in arg or "github.com" in arg.lower():
            raise ValueError(
                f"gh argument {arg!r} looks like a web address; refer to pull "
                "requests and issues by numbers, not web addresses"
            )
        if arg.startswith("--") and "=" in arg:
            raise ValueError(
                f"gh flag {arg!r} must be passed as separate arguments "
                "(--flag value), not --flag=value"
            )
    completed = subprocess.run(  # noqa: S603
        ["gh", command, action, *args, "--repo", repo],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout
