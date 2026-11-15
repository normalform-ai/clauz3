"""Environment-variable read policies built from generic trusted effect facts.

These contracts let an
agent state which environment variables it may inspect — the canonical
credential-exfil guard for any program that touches API keys or other
secrets via ``os.environ``.

Use ``only_vars(allowlist)`` when the set of needed variables is small and
explicit (the strongest guarantee). Use ``never_vars(blocklist)`` when
specific variables are known-sensitive and the program needs flexibility
otherwise. Use ``no_env_reads()`` when the program should not touch the
environment at all.
"""

from cz3tools.env.trusted.effects import read_env

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

EnvRead = effect(read_env)


@contract(description="No restrictions are imposed on reading environment variables.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about reading environment variables."""
    return core_no_guarantees()


@contract(
    description=(
        "The environment tool reads no environment variables (settings such as "
        "API keys)."
    )
)
def no_env_reads() -> ContractSpec:
    """Guarantee the environment tool reads no environment variables."""
    return EnvRead.empty()


@contract(description="The environment tool reads only these variables: {allowlist}.")
def only_vars(allowlist: list[str]) -> ContractSpec:
    """Guarantee every environment variable read is one of the listed names."""
    return EnvRead.all(lambda e: e.name in allowlist)


@contract(description="The environment tool never reads these variables: {blocklist}.")
def never_vars(blocklist: list[str]) -> ContractSpec:
    """Guarantee no environment variable read is one of the listed names."""
    return EnvRead.all(lambda e: e.name not in blocklist)


@contract(
    description=(
        "The environment tool never reads a variable whose name starts with {prefix}."
    )
)
def never_var_prefix(prefix: str) -> ContractSpec:
    """Guarantee no environment variable read has a name starting with ``prefix``.

    Useful for blanket prefixes like ``SECRET_`` or ``AWS_`` without
    enumerating every variable individually. The comparison is case-sensitive.
    """
    return EnvRead.all(lambda e: not e.name.startswith(prefix))


@contract(description="The environment tool reads variables at most {count} times.")
def env_reads_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` environment variable reads occur."""
    return EnvRead.count() <= count
