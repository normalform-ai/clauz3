"""Initialize shared entry-point dependencies before engine identity capture."""

from importlib import import_module

# The CLI and approval components depend on these stdlib modules. Embedders
# initialize the same graph before the coordinator freezes identities.
_STDLIB_DEPENDENCIES = (
    "argparse",
    "asyncio",
    "decimal",
    "secrets",
    "http.server",
    "urllib.request",
    "subprocess",
)


def bootstrap_engine_dependencies() -> None:
    """Load the explicit stdlib dependencies shared by supported entry points.

    This makes cold CLI and prover startup consistent. Identity capture still
    verifies loaded objects and their import plans. Additional eligible stdlib
    modules preloaded by a host can remain in that snapshot; installed packages
    do not gain blanket engine authority.
    """
    for name in _STDLIB_DEPENDENCIES:
        import_module(name)
