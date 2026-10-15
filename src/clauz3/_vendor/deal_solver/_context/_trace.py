from __future__ import annotations

import typing
from collections import Counter
from contextlib import contextmanager


# Not a legal Python function name, so source code cannot collide with it.
LAMBDA_BODY = '<lambda body>'

COMPREHENSION_BODY = '<comprehension body>'
QUANTIFIED_LOOP_BODY = '<quantified loop body>'
BOOLEAN_OPERAND = '<boolean operand>'
GUARDED_EXPRESSION = '<guarded expression>'


class Trace:
    __slots__ = ['_names']
    _names: Counter

    def __init__(self) -> None:
        self._names = Counter()

    @contextmanager
    def guard(self, name: str) -> typing.Iterator[None]:
        self._names[name] += 1
        try:
            yield
        finally:
            self._names[name] -= 1

    def __contains__(self, name: str) -> bool:
        return self._names[name] > 0

    def __repr__(self) -> str:
        return '{n}({r})'.format(
            n=type(self).__name__,
            r=', '.join(sorted(self._names)),
        )
