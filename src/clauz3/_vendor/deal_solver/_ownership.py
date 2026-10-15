"""Conservative write permission for immutable collection snapshots.

Origins are explicit allocation identities, not Z3 or proxy equality. Once a
collection is shared, every snapshot of that origin becomes read-only. This
deliberately does not implement a shared mutable heap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._exceptions import UnsupportedError

if TYPE_CHECKING:
    from ._context import Context
    from ._proxies import ProxySort


MUTATION_ERROR = 'shared or borrowed collection mutation is not supported'


@dataclass(eq=False)
class Origin:
    frame: object
    owner: str | None = None
    shared: bool = False


def is_mutable(value: ProxySort) -> bool:
    from ._proxies import types

    return isinstance(value, (types.list, types.dict, types.set))


def _clone(value: ProxySort) -> ProxySort:
    from ._proxies import UntypedDictSort, UntypedListSort, UntypedSetSort

    if isinstance(value, (UntypedListSort, UntypedDictSort, UntypedSetSort)):
        return type(value)()
    return value.evolve()


class Ownership:
    def __init__(self) -> None:
        # Keep strong references: an id must not be reused during this proof.
        self._origins: dict[int, tuple[ProxySort, Origin]] = {}
        self._captures: dict[object, set[str]] = {}

    def origin(self, value: ProxySort) -> Origin | None:
        item = self._origins.get(id(value))
        return None if item is None else item[1]

    def fresh(self, value: ProxySort, *, ctx: Context) -> ProxySort:
        if not is_mutable(value):
            return value
        # Some copy/constructor models return self. Separate the proxy before
        # allocating an origin, so a Python copy cannot relabel its input.
        value = _clone(value)
        self._origins[id(value)] = (value, Origin(frame=ctx.frame))
        return value

    def share(self, value: ProxySort) -> None:
        origin = self.origin(value)
        if origin is not None:
            origin.shared = True

    def bind(self, name: str, value: ProxySort, *, ctx: Context) -> None:
        origin = self.origin(value)
        if origin is None:
            return
        if origin.frame is not ctx.frame:
            origin.shared = True
        elif origin.owner is None:
            origin.owner = name
        elif origin.owner != name:
            origin.shared = True
        if name in self._captures.get(ctx.frame, ()):
            origin.shared = True

    def capture(self, name: str, *, ctx: Context) -> None:
        self._captures.setdefault(ctx.frame, set()).add(name)
        value = ctx.scope.get(name)
        if value is not None:
            self.share(value)

    def require_write(self, value: ProxySort, name: str, *, ctx: Context) -> None:
        if not is_mutable(value):
            return
        origin = self.origin(value)
        if (
            origin is None
            or origin.shared
            or origin.frame is not ctx.frame
            or origin.owner != name
        ):
            raise UnsupportedError(MUTATION_ERROR, name)

    def replace(self, before: ProxySort, after: ProxySort) -> None:
        origin = self.origin(before)
        if origin is not None and is_mutable(after):
            existing = self.origin(after)
            if existing is not None and existing is not origin:
                # A model returned another collection's existing proxy. Never
                # transfer its owner or erase an already-shared origin.
                origin.shared = existing.shared = True
                return
            self._origins[id(after)] = (after, origin)

    def merge(self, result: ProxySort, left: ProxySort, right: ProxySort) -> None:
        first, second = self.origin(left), self.origin(right)
        if first is not None and first is second:
            self._origins[id(result)] = (result, first)
        else:
            # A joined snapshot may refer to either root. It cannot acquire
            # ownership, and neither original can safely mutate through it.
            self.share(left)
            self.share(right)


def capture_names(node, *, ctx: Context) -> None:
    """Seal captured bindings, including names assigned after declaration.

    Nested scopes can retain a snapshot across a caller branch. Exclude the
    callable's own locals; conservatively inspect all remaining name uses.
    """
    import astroid

    for use in node.nodes_of_class(astroid.Name):
        if use.name not in node.locals:
            ctx.ownership.capture(use.name, ctx=ctx)
