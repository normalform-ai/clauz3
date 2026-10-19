"""Methods the subset admits on its own values.

The prover resolves attribute access through each proxy sort's ``methods``
table (``deal_solver._proxies._methods.Methods``), and most entries there route
to an ``unsupported`` stub that raises. The ones with a real implementation are
genuinely provable -- ``'sandbox/x'.startswith('sandbox')`` proves today -- so
an interpreter that refuses all attribute access on data is *narrower than the
proved subset*, and flipping it on would stop already-proved programs running.

"Provable" here means the prover has a model, not that every receiver binding
is supported. The interpreter preserves Python's shared mutable references;
the prover uses collection snapshots and refuses writes after sharing. Thus
``b = a; b.clear()`` is executable here but is refused by the proof's ownership
gate. Saved methods, parameter/capture bindings, and discarded loop contexts
have additional proof restrictions. This table describes the method surface,
not permission to mutate an arbitrary collection binding.

That is the mirror of the ``LIBRARY_RETURNS_OUTSIDE_DOMAIN`` gap: provable but
not interpretable. It is handled the same way -- an explicit table, joined
against the prover's registry by ``tests/test_interp_parity.py`` so the two
cannot drift silently.

Implementations are CPython's own, which is safe here for the same reason the
operators are: the receiver is always subset data (``str``/``list``/``dict``/
``set``/``tuple``/``int``/``float``), never an attacker-defined class, so there
is no ``__getattr__`` or descriptor to dispatch into. Results are domain-checked
by the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Method names the prover models with a real implementation, per receiver type.
#:
#: Deliberately spelled out rather than derived from the prover at import time:
#: this table is a security-relevant surface and should be reviewable as a list,
#: with a test proving it matches. ``bool`` is absent on purpose -- the prover's
#: bool sort models no methods, and ``bool`` must be checked before ``int``
#: because it is a subclass.
MODELLED_METHODS: dict[type, frozenset[str]] = {
    str: frozenset({"count", "endswith", "find", "index", "split", "startswith"}),
    list: frozenset({"append", "clear", "copy", "count", "extend", "index"}),
    tuple: frozenset({"count", "index"}),
    set: frozenset(
        {
            "add",
            "clear",
            "copy",
            "difference",
            "difference_update",
            "discard",
            "intersection",
            "intersection_update",
            "isdisjoint",
            "issubset",
            "issuperset",
            "pop",
            "remove",
            "symmetric_difference",
            "symmetric_difference_update",
            "union",
            "update",
        }
    ),
    dict: frozenset({"clear", "copy", "get", "pop"}),
    int: frozenset({"conjugate", "denominator", "imag", "numerator", "real"}),
    float: frozenset({"conjugate", "imag", "is_integer", "real"}),
}

#: Entries that are attributes rather than calls (``(5).numerator``), mirroring
#: the prover's ``prop=True`` registrations.
PROPERTIES: frozenset[str] = frozenset({"denominator", "imag", "numerator", "real"})


def receiver_type(value: Any) -> type | None:
    """The table key for ``value``, or None if it has no method surface.

    ``bool`` is matched before ``int`` and maps to nothing: the prover models no
    methods on its bool sort, so neither does this.
    """
    if isinstance(value, bool):
        return None
    for candidate in (str, list, tuple, set, frozenset, dict, int, float):
        if isinstance(value, candidate):
            return frozenset if candidate is frozenset else candidate
    return None


def lookup(value: Any, name: str) -> Callable[..., Any] | None:
    """Resolve ``value.name`` to a CPython implementation, or None."""
    key = receiver_type(value)
    if key is frozenset:
        key = set
    if key is None:
        return None
    if name not in MODELLED_METHODS.get(key, frozenset()):
        return None
    attribute = getattr(type(value), name, None)
    if attribute is None:  # pragma: no cover - guarded by the parity test
        return None
    return attribute  # type: ignore[no-any-return]


def _check_table_is_resolvable() -> None:
    """Every listed name must exist on its CPython type.

    Import-time rather than test-time: a typo here would silently narrow the
    subset back to "no methods", which is the failure this module exists to fix.
    """
    for receiver, names in MODELLED_METHODS.items():
        missing = [name for name in names if not hasattr(receiver, name)]
        if missing:  # pragma: no cover - defensive
            raise RuntimeError(
                f"MODELLED_METHODS lists {missing!r} for {receiver.__name__}, "
                "which CPython does not provide",
            )


_check_table_is_resolvable()
