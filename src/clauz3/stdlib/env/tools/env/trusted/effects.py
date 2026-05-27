"""Trusted environment-variable read effect.

``read_env`` returns the named variable's value, or the empty string if
unset. Carries the ``read`` and ``env`` markers so contracts in
``contracts.py`` can constrain *which* variables a program may read —
the primary credential-exfil guard for any agent that touches secrets
via the environment (the conventional shape for API keys).
"""

import os

import deal


@deal.pre(lambda name: len(name) > 0, message="env var name must be non-empty")
@deal.has("read", "env", "global", "trusted")
def read_env(name: str) -> str:
    """Return the value of env var ``name``, or the empty string if unset.

    Recorded under the ``read`` and ``env`` markers. The contract layer
    can restrict reads by name allowlist / blocklist or a total count.
    """
    return os.environ.get(name, "")
