"""Trusted GitHub-CLI effect: a real (non-mock) ``gh`` subprocess.

``gh`` runs a single GitHub CLI invocation of the shape
``gh <command> <action> --repo <repo> <args...>`` and returns its stdout. It
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
    ``copilot``, which runs a separately installed program, and ``api``:
    ``gh api`` ignores ``--repo`` for a literal endpoint and accepts any
    method, so it can reach any repository or account. Aliases and
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


def is_owner_name_slug(repo: str) -> bool:
    """Whether ``repo`` is exactly ``owner/name`` (one ``/``, no empty parts).

    gh reads a three-part ``HOST/OWNER/REPO`` slug as naming a host, which
    would let ``only_owner("ghe.example.com")`` admit any owner on that host.

    >>> [is_owner_name_slug(r) for r in ["a/b", "h/a/b", "a/", "/b", "ab"]]
    [True, False, False, False, False]
    """
    return repo.count("/") == 1 and not repo.startswith("/") and not repo.endswith("/")


def is_canonical_action(command: str, action: str) -> bool:
    """Whether ``action`` is gh's canonical name rather than an action alias.

    gh 2.101 accepts these aliases (from ``gh help reference``): ``ls`` for
    ``list``, ``new`` for ``create``, ``co`` for ``pr checkout``, ``remove``
    for ``secret``/``variable delete``, ``show`` and ``add`` for ``skill
    preview``/``install``. Refusing them keeps one name per action, so
    ``never_actions(["delete"])`` cannot be bypassed with ``remove``.
    """
    return action not in ["ls", "new", "co", "remove", "show"] and not (
        command == "skill" and action == "add"
    )


def reaches_second_repository(command: str, action: str) -> bool:
    """Whether the action takes a second repository as an ordinary argument."""
    return (command == "issue" and action == "transfer") or (
        command == "label" and action == "clone"
    )


def is_plain_action(action: str) -> bool:
    """Whether ``action`` is a word, not a flag, ``--flag=value`` or a link.

    ``action`` reaches gh's command line like any argument, so a flag such as
    ``--org=otherorg`` there would widen the command just as in ``args``.
    """
    return not action.startswith("-") and "=" not in action and "://" not in action


def has_no_scope_flags(args: list[str]) -> bool:
    """Whether ``args`` has none of the exact flags that widen scope past --repo.

    ``--org``/``-o``, ``--user``/``-u``, ``--owner`` and its ``--repo-owner``,
    ``--source-owner`` and ``--target-owner`` variants, ``--repos``,
    ``--hostname``, ``--source`` and ``--branch-repo`` point a command at
    other accounts, organizations, hosts or repositories, and an
    agent-supplied ``--repo``/``-R`` would add to or compete with the one
    the effect appends. A bare ``--`` would turn that appended ``--repo``
    into an ordinary argument. ``-o`` and ``-u`` are also short forms of
    ``repo read-file --output``, ``repo clone --upstream-remote-name`` and
    ``repo set-default --unset``; those stay available by their long names.
    """
    return (
        "--org" not in args
        and "-o" not in args
        and "--user" not in args
        and "-u" not in args
        and "--repos" not in args
        and "--owner" not in args
        and "--repo-owner" not in args
        and "--source-owner" not in args
        and "--target-owner" not in args
        and "--hostname" not in args
        and "--source" not in args
        and "--branch-repo" not in args
        and "--repo" not in args
        and "-R" not in args
        and "--" not in args
    )


def _argument_error(arg: str) -> str | None:
    """Why ``arg`` is refused, or ``None``; also catches spellings a
    precondition cannot see (``-Rother/repo``, ``-wR``, ``--org=x``, URLs).

    >>> [_argument_error(a) is None for a in ["42", "--squash", "-w", "-Rx/y"]]
    [True, True, True, False]
    """
    if "://" in arg or "github.com" in arg.lower():
        return (
            "looks like a web address; refer to pull requests and issues by "
            "numbers, not web addresses"
        )
    if arg.startswith("--") and "=" in arg:
        return "must be passed as separate arguments (--flag value), not --flag=value"
    if not has_no_scope_flags([arg]):
        return "widens the command past its --repo repository"
    short = arg[1:] if arg.startswith("-") and not arg.startswith("--") else ""
    if "o" in short or "u" in short or "R" in short:
        return "contains -o, -u or -R, which widen the command past --repo"
    if len(short) > 1 and "S" in short:
        return "groups -S (search) with other letters; pass -S as its own argument"
    return None


@clauz3.requires(
    lambda command, action, args, repo: is_owner_name_slug(repo),
    message="repo must be an owner/name slug (no host prefix)",
)
@clauz3.requires(
    lambda command, action, args, repo: has_no_scope_flags(args),
    message="args must not widen the command past its --repo repository",
)
@clauz3.requires(
    lambda command, action, args, repo: is_plain_action(action),
    message="action must be a word, not a flag or a web address",
)
@clauz3.requires(
    lambda command, action, args, repo: is_canonical_action(command, action),
    message="action must be gh's canonical name, not an alias such as ls or remove",
)
@clauz3.requires(
    lambda command, action, args, repo: not reaches_second_repository(command, action),
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
    """Run ``gh <command> <action> --repo <repo> <args...>`` and return stdout.

    Arguments are passed as an explicit vector, so no field is interpreted by a
    shell. ``repo`` is always supplied as ``--repo`` so every call is scoped to
    a single repository, which ``only_repo`` / ``only_owner`` can then pin. It
    comes before the agent's ``args`` (gh accepts flags anywhere), so a
    trailing value-taking flag such as ``--body`` cannot swallow it. ``repo``
    must be an ``owner/name`` slug: gh reads ``HOST/OWNER/REPO`` as naming a
    host.

    Refused, as preconditions and again in the body (Deal's runtime checks
    are optional, so the body never relies on them):

    - a ``command`` that is not built in (``is_builtin_command``): aliases,
      extensions and ``gh api``, which can reach any endpoint;
    - an ``action`` that is a flag, ``--flag=value`` or link
      (``is_plain_action``); the body also applies the ``args`` checks to it;
    - an action alias (``is_canonical_action``): use ``list``, not ``ls``;
      ``delete``, not ``remove``;
    - ``issue transfer`` and ``label clone``, which take a second repository
      as an ordinary argument;
    - flags that widen the command past ``--repo`` (``has_no_scope_flags``),
      an agent-supplied ``--repo``/``-R``, and a bare ``--``.

    Refused only when the program runs (the prover cannot check them, so an
    earlier call in the same program may already have run):

    - web addresses: gh resolves a URL against the URL's own repository,
      ignoring ``--repo``, so any argument containing ``://`` or
      ``github.com`` (any letter case) is refused. Refer to pull requests and
      issues by number; put text with links in a file and pass
      ``--body-file``;
    - ``--flag=value``: pass long flags as separate arguments
      (``["--body", "text"]``), so ``no_admin_override`` can check for
      ``"--admin"`` by exact membership without ``--admin=true`` slipping
      past it (gh accepts no flag abbreviations);
    - short-option groups containing ``o``, ``u`` or ``R`` (``-Rother/repo``),
      or ``S`` with other letters (``-wS``); ``-S`` alone is allowed, and
      ``only_repo`` / ``only_owner`` forbid it.
    """
    if not (len(command) > 0 and len(action) > 0 and len(repo) > 0):
        raise ValueError("command, action, and repo must be non-empty")
    if not is_owner_name_slug(repo):
        raise ValueError(f"gh repo {repo!r} must be an owner/name slug")
    if not is_builtin_command(command):
        raise ValueError(f"gh command {command!r} is not a built-in gh command")
    if reaches_second_repository(command, action):
        raise ValueError("issue transfer and label clone reach a second repository")
    if not is_plain_action(action):
        raise ValueError(f"gh action {action!r} must be a word, not a flag or link")
    action_error = _argument_error(action)
    if action_error is not None:
        raise ValueError(f"gh action {action!r} is refused: it {action_error}")
    if not is_canonical_action(command, action):
        raise ValueError(f"gh action {action!r} is an alias; use its canonical name")
    for arg in args:
        reason = _argument_error(arg)
        if reason is not None:
            raise ValueError(f"gh argument {arg!r} is refused: it {reason}")
    completed = subprocess.run(  # noqa: S603
        ["gh", command, action, "--repo", repo, *args],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout
