"""Environment-variable read policies built from generic trusted effect facts.

Env reads carry the ``read`` and ``env`` markers. These contracts let an
agent state which environment variables it may inspect — the canonical
credential-exfil guard for any program that touches API keys or other
secrets via ``os.environ``.

Use ``only_vars(allowlist)`` when the set of needed variables is small and
explicit (the strongest guarantee). Use ``never_vars(blocklist)`` when
specific variables are known-sensitive and the program needs flexibility
otherwise. Use ``no_env_reads()`` when the program should not touch the
environment at all.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

EnvRead = effect("read_env")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about env-read effects."""
    return core_no_guarantees()


@contract
def no_env_reads() -> ContractSpec:
    """Guarantee the program reads no environment variables."""
    return EnvRead.empty()


@contract
def only_vars(allowlist: list[str]) -> ContractSpec:
    """Guarantee every env-var name read is in ``allowlist``."""
    return EnvRead.all(lambda e: e.name in allowlist)


@contract
def never_vars(blocklist: list[str]) -> ContractSpec:
    """Guarantee no env-var name read is in ``blocklist``."""
    return EnvRead.all(lambda e: e.name not in blocklist)


@contract
def never_var_prefix(prefix: str) -> ContractSpec:
    """Guarantee no env-var name read starts with ``prefix``.

    Useful for blanket prefixes like ``SECRET_`` or ``AWS_`` without
    enumerating every variable individually.
    """
    return EnvRead.all(lambda e: not e.name.startswith(prefix))


@contract
def env_reads_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` env-var reads occur."""
    return EnvRead.count() <= count
