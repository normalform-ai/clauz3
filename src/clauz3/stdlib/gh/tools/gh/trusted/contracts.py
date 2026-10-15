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
READ_ONLY_ACTIONS = [
    "view",
    "list",
    "status",
    "diff",
    "checks",
]


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about gh effects."""
    return core_no_guarantees()


@contract
def no_gh() -> ContractSpec:
    """Guarantee the program makes no GitHub-CLI calls."""
    return Gh.empty()


@contract
def read_only() -> ContractSpec:
    """Guarantee every gh invocation uses a read-only verb.

    Pins each ``action`` to ``READ_ONLY_ACTIONS``, so the program cannot mutate
    any GitHub object (no ``merge`` / ``close`` / ``delete`` / ``edit`` ...).
    """
    return Gh.all(lambda e: e.action in READ_ONLY_ACTIONS)


@contract
def only_commands(allowlist: list[str]) -> ContractSpec:
    """Guarantee every gh command (the GitHub object) is in ``allowlist``.

    For example ``only_commands(["pr", "issue"])`` lets an agent work with pull
    requests and issues but not ``repo``, ``release``, ``secret``, ...
    """
    return Gh.all(lambda e: e.command in allowlist)


@contract
def never_actions(blocklist: list[str]) -> ContractSpec:
    """Guarantee no gh action (the verb) is in ``blocklist``.

    Useful to veto specific mutating verbs (``merge``, ``close``, ``delete``)
    while leaving the rest of the CLI available.
    """
    return Gh.all(lambda e: e.action not in blocklist)


@contract
def only_repo(repo: str) -> ContractSpec:
    """Guarantee every gh invocation targets the repository ``repo``.

    ``repo`` is the full ``owner/name`` slug passed as ``--repo``.
    """
    return Gh.all(lambda e: e.repo == repo)


@contract
def only_owner(owner: str) -> ContractSpec:
    """Guarantee every targeted repository belongs to ``owner``.

    Matches any ``owner/<name>`` slug, so an agent can range over one
    organization's repositories without naming each one.
    """
    prefix = owner + "/"
    return Gh.all(lambda e: e.repo.startswith(prefix))


@contract
def runs_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` gh invocations occur."""
    return Gh.count() <= count
