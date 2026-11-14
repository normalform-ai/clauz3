"""GitHub-CLI policies built from generic trusted effect facts.

Every ``gh`` invocation is recorded as the ``gh`` effect with its
``command`` (the GitHub object, e.g. ``pr``), ``action`` (the verb, e.g.
``view``), and target ``repo`` (see ``effects.py``). These contracts let an
agent state which verbs it may run, which objects it may touch, and which
repository it may target.

``read_only`` is the headline guard: it pins every invocation to a fixed set of
inspection verbs, so an agent handed the GitHub CLI can browse pull requests,
issues, and runs but cannot merge, close, delete, or otherwise mutate the
remote.
"""

from tools.gh.trusted.effects import gh

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Gh = effect(gh)

#: GitHub-CLI verbs that only read state. ``read_only`` pins every ``action``
#: to this set. Deliberately excludes mutating verbs such as ``create``,
#: ``edit``, ``merge``, ``close``, ``comment``, ``delete``, and ``ready``.
#: The ``read_only`` description names these literally; keep them in sync.
READ_ONLY_ACTIONS = [
    "view",
    "list",
    "status",
    "diff",
    "checks",
]


@contract(description="No restrictions are imposed on GitHub commands.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about GitHub commands."""
    return core_no_guarantees()


@contract(description="No GitHub commands are run.")
def no_gh() -> ContractSpec:
    """Guarantee the program runs no GitHub commands."""
    return Gh.empty()


@contract(
    description=(
        "GitHub commands can only look at things (view, list, status, diff, "
        "checks); nothing on GitHub is created, changed, merged, closed or deleted."
    )
)
def read_only() -> ContractSpec:
    """Guarantee GitHub commands only look at things and change nothing.

    Pins each ``action`` (the verb) to ``READ_ONLY_ACTIONS``, so the program
    cannot run ``merge`` / ``close`` / ``delete`` / ``edit`` ... on any GitHub
    object. The ``command`` and extra ``args`` are not constrained; the
    guarantee relies on the built-in gh commands, so a locally configured gh
    alias or extension whose second word is a read-only verb is not covered.
    Stack ``only_commands`` to exclude those.
    """
    return Gh.all(lambda e: e.action in READ_ONLY_ACTIONS)


@contract(
    description=(
        "GitHub commands work only on: {allowlist} (for example pr means pull "
        "requests)."
    )
)
def only_commands(allowlist: list[str]) -> ContractSpec:
    """Guarantee GitHub commands work only on the listed kinds of object.

    Every ``command`` (the GitHub object, the first word after ``gh``) must be
    in ``allowlist``. For example ``only_commands(["pr", "issue"])`` lets an
    agent work with pull requests and issues but not ``repo``, ``release``,
    ``secret``, ...
    """
    return Gh.all(lambda e: e.command in allowlist)


@contract(description="No GitHub command uses any of these actions: {blocklist}.")
def never_actions(blocklist: list[str]) -> ContractSpec:
    """Guarantee no GitHub command uses any of the listed actions.

    Every ``action`` (the verb, the second word after ``gh``) must be outside
    ``blocklist``. Useful to veto specific mutating verbs (``merge``,
    ``close``, ``delete``) while leaving the rest of the CLI available. Only the
    action word is compared: ``gh pr merge --delete-branch`` still deletes a
    branch even when ``delete`` is blocked.
    """
    return Gh.all(lambda e: e.action not in blocklist)


@contract(
    description=(
        "Every GitHub command is pointed at the repository {repo}. A pull "
        "request or issue link given to a command can still reach another "
        "repository."
    )
)
def only_repo(repo: str) -> ContractSpec:
    """Guarantee every GitHub command is given the repository ``repo``.

    ``repo`` is the full ``owner/name`` slug passed as ``--repo``. gh resolves
    a pull-request or issue URL argument against the URL's own repository, so
    this does not stop an argument such as
    ``https://github.com/other/repo/pull/1`` from reaching another repository.
    """
    return Gh.all(lambda e: e.repo == repo)


@contract(
    description=(
        "Every GitHub command is pointed at a repository owned by {owner}. A pull "
        "request or issue link given to a command can still reach another "
        "repository."
    )
)
def only_owner(owner: str) -> ContractSpec:
    """Guarantee every GitHub command is given a repository owned by ``owner``.

    Matches any ``owner/<name>`` slug passed as ``--repo``, so an agent can
    range over one organization's repositories without naming each one. As
    with ``only_repo``, a pull-request or issue URL argument is resolved
    against the URL's own repository and is not covered.
    """
    prefix = owner + "/"
    return Gh.all(lambda e: e.repo.startswith(prefix))


@contract(description="At most {count} GitHub commands are run.")
def runs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` GitHub commands are run."""
    return Gh.count() <= count


@contract(
    description=(
        "No GitHub command overrides branch protection or required reviews (--admin)."
    )
)
def no_admin_override() -> ContractSpec:
    """Guarantee no GitHub command uses administrator override (``--admin``).

    ``gh pr merge --admin`` merges a pull request that does not meet its
    branch-protection requirements, such as required reviews or checks. The
    check is exact membership of ``"--admin"`` in ``args``; this is sound
    because the ``gh`` effect rejects ``--flag=value`` spellings at run time,
    and ``--admin`` has no short form. State this unless the user asked for an
    admin override.
    """
    return Gh.all(lambda e: "--admin" not in e.args)
