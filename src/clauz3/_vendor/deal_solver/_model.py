from __future__ import annotations

import typing

import z3

from ._exceptions import UnsupportedError
from ._strings import is_string_expr, string_value


def _store(items, k, v):
    if isinstance(items, set):
        items.add(k)
    else:
        items[k] = v
    return items


GLOBALS = dict(
    Unit=lambda x: [x],
    Seq=lambda _: [],
    Empty=lambda x: x,
    Concat=lambda x, y: x + y,
    K=lambda x, y: set() if y is False else dict(),
    Store=_store,
    new=lambda _, val: val,

    # types
    Int=int,
    Real=float,
    FPSort=lambda x, y: float,
)


def _decode(value: z3.ExprRef) -> object:
    """Decode structure before the legacy primitive-display fallback.

    A sequence of strings and a string are different sorts even though both
    display with Unit/Concat. Inspecting the AST preserves that distinction
    without evaluating datatype constructor names or adding eval bindings.
    """
    if is_string_expr(value):
        try:
            return string_value(value)
        except UnsupportedError:
            return str(value)
    if z3.is_app_of(value, z3.Z3_OP_SEQ_EMPTY):
        return []
    if z3.is_app_of(value, z3.Z3_OP_SEQ_UNIT):
        return [_decode(value.arg(0))]
    if z3.is_app_of(value, z3.Z3_OP_SEQ_CONCAT):
        parts = [_decode(child) for child in value.children()]
        if all(isinstance(part, list) for part in parts):
            return [item for part in parts for item in part]
        return str(value)
    if z3.is_app_of(value, z3.Z3_OP_CONST_ARRAY):
        return set() if z3.is_false(value.arg(0)) else {}
    if z3.is_app_of(value, z3.Z3_OP_STORE):
        items = _decode(value.arg(0))
        key = _decode(value.arg(1))
        item = _decode(value.arg(2))
        if isinstance(items, (set, dict)):
            try:
                return _store(items, key, item)
            except TypeError:
                # A model outside the Python container domain should remain
                # printable, including an unhashable symbolic key.
                pass
        return str(value)
    if z3.is_app_of(value, z3.Z3_OP_DT_CONSTRUCTOR):
        name = str(value.decl().name())
        if name == 'new' and value.num_args() == 2:
            # The dictionary model wraps each payload in (exists, value).
            return _decode(value.arg(1))
        if value.num_args():
            fields = ', '.join(repr(_decode(child)) for child in value.children())
            return f'{name}({fields})'
        return str(value)
    try:
        return eval(repr(value), GLOBALS)
    except (NameError, SyntaxError):
        return str(value)


class Model:
    _model: z3.ModelRef

    def __init__(self, model: z3.ModelRef) -> None:
        self._model = model

    def __iter__(self) -> typing.Iterator[tuple[str, object]]:
        for decl in self._model.decls():
            name = decl.name()
            z3_val = self._model[decl]
            if isinstance(z3_val, z3.FuncInterp):  # pragma: no cover
                continue
            yield name, _decode(z3_val)

    def __bool__(self) -> bool:
        return len(self._model) != 0

    def __str__(self) -> str:
        return ', '.join(f'{k}={v!r}' for k, v in sorted(self))

    def __repr__(self) -> str:
        return f'{type(self).__name__}({repr(self._model)})'
