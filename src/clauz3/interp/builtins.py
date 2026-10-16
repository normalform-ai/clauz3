"""The builtins an interpreted program may call.

Deliberately an *allowlist of implementations*, not a filtered copy of
``builtins``. The runtime executor has to enumerate what to remove from
``vars(builtins)`` and keeps discovering new entries to remove; here the
default is that a builtin does not exist, and adding one is a visible edit.

The set mirrors what the prover models in ``deal_solver._funcs`` -- ``abs``,
``bool``, ``dict``, ``float``, ``int``, ``len``, ``list``, ``max``, ``min``,
``ord``, ``print``, ``set``, ``str``, ``sum`` -- plus ``range``, which the
prover handles structurally in ``for`` loops. A builtin the prover cannot model
must not be callable at runtime, or a program could execute down a path the
proof never considered.

``tests/test_interp_parity.py`` asserts that correspondence against
``FUNCTIONS`` directly, with ``range`` as the single recorded exemption. An
earlier draft carried ``sorted``, which the prover does not model at all -- the
same "runs but cannot be proved" gap this design exists to close, reintroduced
one table over. Prose stating a rule is not the same as a test enforcing it.
"""

from __future__ import annotations

import builtins as _py_builtins
from collections.abc import Callable, Iterable
from typing import Any

from .errors import UnsupportedNode
from .values import BuiltinFunction

#: Cap on values ``range()`` may produce, so an interpreted loop cannot become
#: an unbounded one. The prover only unrolls ``range(N)`` for a literal ``N``.
MAX_RANGE = 100_000


def _require_data(name: str, *values: Any) -> None:
    from clauz3.row import Row

    from .values import CONTAINER_TYPES, SCALAR_TYPES

    for value in values:
        if value is None or isinstance(value, (*SCALAR_TYPES, *CONTAINER_TYPES, Row)):
            continue
        raise UnsupportedNode(
            f"{name}() does not accept {type(value).__name__} in the subset",
        )


def _b_len(value: Any) -> int:
    _require_data("len", value)
    return len(value)


def _b_abs(value: Any) -> Any:
    _require_data("abs", value)
    return abs(value)


def _b_int(value: Any = 0, /) -> int:
    _require_data("int", value)
    return int(value)


def _b_float(value: Any = 0.0, /) -> float:
    _require_data("float", value)
    return float(value)


def _b_str(value: Any = "", /) -> str:
    _require_data("str", value)
    return str(value)


def _b_bool(value: Any = False, /) -> bool:
    _require_data("bool", value)
    return bool(value)


def _b_ord(value: Any) -> int:
    _require_data("ord", value)
    return ord(value)


def _b_sum(items: Any, start: Any = 0) -> Any:
    _require_data("sum", items, start)
    return sum(items, start)


def _b_min(*args: Any) -> Any:
    _require_data("min", *args)
    return min(*args)


def _b_max(*args: Any) -> Any:
    _require_data("max", *args)
    return max(*args)


def _b_list(items: Iterable[Any] = ()) -> list[Any]:
    _require_data("list", items)
    return list(items)


def _b_set(items: Iterable[Any] = ()) -> set[Any]:
    _require_data("set", items)
    return set(items)


def _b_dict(items: Any = None, /, **kwargs: Any) -> dict[Any, Any]:
    # ``**kwargs`` is checked too. Nothing escaped through it -- calls dispatch
    # on interpreter value types and ``marshal_out`` refuses non-domain values
    # -- but it was the one builtin whose inputs were not fully enumerated, and
    # "allowlist the inputs" is the rule this module states for every other one.
    _require_data("dict", *kwargs.values())
    if items is None:
        return dict(**kwargs)
    _require_data("dict", items)
    return dict(items, **kwargs)


def _b_range(*args: Any) -> list[int]:
    """``range`` materialised as a bounded list.

    The subset has no lazy iterators, and a bounded list is what the prover's
    unrolling models. The cap keeps an interpreted loop finite.

    Only the cap is a subset limit. ``range('a')`` and ``range(1, 5, 0)`` are
    the program calling a builtin wrongly, exactly like ``ord('ab')``: CPython's
    own ``TypeError``/``ValueError`` propagates to ``call``, which reports it as
    a program fault (``clauz3 run`` exit 1), not a subset refusal (exit 2).

    >>> _b_range(1, 7, 2)
    [1, 3, 5]
    >>> _b_range(1, 5, 0)
    Traceback (most recent call last):
    ...
    ValueError: range() arg 3 must not be zero
    """
    _require_data("range", *args)
    values = _py_builtins.range(*args)
    # Computed from start/stop/step rather than via ``len()``: ``len`` on a
    # range wider than a C ssize_t raises OverflowError before the bound below
    # can reject it, which would surface as an unclassified crash.
    # Ceiling division, which is sign-agnostic: for step > 0 a negative span
    # floors to <= 0, and for step < 0 a positive span does the same. An earlier
    # draft wrote this as a ternary whose two arms were identical, advertising a
    # distinction that does not exist.
    count = max(0, -(-(values.stop - values.start) // values.step))
    if count > MAX_RANGE:
        raise UnsupportedNode(
            f"range() of {count} exceeds the interpreter bound of {MAX_RANGE}",
        )
    return list(values)


def _b_print(*args: Any, **kwargs: Any) -> None:
    """``print`` is modelled as a no-op by the prover; keep it observable here.

    Keyword arguments are refused rather than dropped. Accepting ``sep=''`` and
    then printing with the default separator is a silent behaviour difference
    from ``clauz3 run`` -- exactly the failure mode this design exists to
    remove. An unsupported call should be a refusal.
    """
    if kwargs:
        raise UnsupportedNode(
            f"print() keyword arguments are not in the subset: "
            f"{', '.join(sorted(kwargs))}",
        )
    _require_data("print", *args)
    _py_builtins.print(*args)


_IMPLS: dict[str, Callable[..., Any]] = {
    "abs": _b_abs,
    "bool": _b_bool,
    "dict": _b_dict,
    "float": _b_float,
    "int": _b_int,
    "len": _b_len,
    "list": _b_list,
    "max": _b_max,
    "min": _b_min,
    "ord": _b_ord,
    "print": _b_print,
    "range": _b_range,
    "set": _b_set,
    "str": _b_str,
    "sum": _b_sum,
}

BUILTINS: dict[str, BuiltinFunction] = {
    name: BuiltinFunction(name=name, impl=impl) for name, impl in _IMPLS.items()
}
