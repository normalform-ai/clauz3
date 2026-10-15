"""The decorator that marks a trusted effect boundary.

A trusted effect is the one thing an agent-authored program cannot do for
itself: reach outside the process. The prover never runs such a body. It
records the call as a symbolic fact, requires the function's preconditions at
the call site, and materializes a return value from the annotation --- see
``clauz3._vendor.deal_solver._proxies._func``. Everything a contract can say
about a program is said about those facts.

Deciding *which* functions get that treatment is therefore the most
consequential judgement in the pipeline, and until this module existed it was
made by matching the literal source text ``deal.has``::

    # deal/linter/_extractors/contracts.py
    SUPPORTED_CONTRACTS = frozenset({... 'deal.has', ...})

That is a test of how a decorator was *spelled*, not of what it *is* --- the
same mistake issue #129 found on the guarantee side, where
:mod:`clauz3.decorators` replaced it with resolution through the module's
import bindings. On the effect side it was worse, because the failure is
silent and fails open: a trusted layer that wrote ``import deal as d`` and
``@d.has("trusted")`` got a function deal enforces at runtime, that
``deal lint`` checks, that reads as a trusted effect to any human --- and that
the prover quietly inlined. No fact was recorded, so ``emails.only([...])``
held vacuously and a program sending elsewhere reported ``proved!``.

:func:`trusted` takes no arguments, because there is nothing to say. A
function either is the boundary or it is not, and the decorator says which.
The marker strings it replaces (``@deal.has("read", "trusted")``) carried two
unrelated vocabularies in one tuple:

* deal's fixed io vocabulary --- ``io``, ``network``, ``read``, ``write``,
  ``syscall``, ``stdout`` --- which deal enforces at runtime and ``deal lint``
  checks against the body. Those belong to deal and stay with deal: a trusted
  layer that wants that second layer of defence declares ``@deal.has(...)``
  alongside, and clauz3 neither reads it nor needs it.
* open-ended clauz3 relation names --- ``gh``, ``db_read``, ``trusted`` ---
  which named nothing deal understood and existed only so a contract could say
  ``effect("db_read")``. A contract now names the trusted function itself, and
  groups several with :func:`clauz3.spec.effect`'s remaining arguments
  (``effect("edit_file", "append_file")``), which puts the grouping in the
  contract that depends on it instead of scattering a string across the
  declarations it happens to span.

``@deal.has(...)`` carrying at least one marker is still honoured as a
boundary, so a trusted layer written before this decorator existed keeps
proving. New layers should use :func:`trusted`.
"""

from collections.abc import Callable
from typing import TypeVar, overload

F = TypeVar("F", bound=Callable[..., object])


@overload
def mock(func: F) -> F: ...


@overload
def mock(func: None = None) -> Callable[[F], F]: ...


def mock(func: F | None = None) -> F | Callable[[F], F]:
    """Declare that an effect implementation is a mock, for discovery and UI.

    This declaration does not grant trusted status, replace the function body,
    suppress side effects, or bypass proof/approval. Use it alongside
    ``@clauz3.trusted`` in a reviewed library. Discovery reads the declaration
    from source without importing the library; it is not a safety certificate.
    """
    if func is None:
        return mock
    if not callable(func):
        raise TypeError("@clauz3.mock takes no arguments")
    return func


@overload
def trusted(func: F) -> F: ...


@overload
def trusted(func: None = None) -> Callable[[F], F]: ...


def trusted(func: F | None = None) -> F | Callable[[F], F]:
    """Mark a trusted function as the side-effecting boundary.

    Only code under a trusted root may carry this decorator; the prover refuses
    it in agent-authored source (:func:`clauz3.prover.validate_untrusted_source`),
    because declaring an effect is what the trusted layer is *for*. An agent
    that could mark its own function as a boundary would hand the prover a body
    it must not read and facts it never performed.

    At runtime it is inert, like :func:`clauz3.guarantee`. Runtime enforcement
    of side effects is deal's job, and a trusted layer that wants it says so
    itself::

        @deal.pre(lambda path: len(path) > 0)
        @deal.has("read", "io")       # optional: deal's runtime + lint layer
        @clauz3.trusted               # the clauz3 boundary
        def read_file(path: str) -> str: ...

    Both spellings are accepted, with or without the empty call:

    >>> @trusted
    ... def send_email(addr: str, msg: str) -> None: ...
    >>> @trusted()
    ... def also_fine(path: str) -> str: ...
    """

    if func is None:
        return trusted
    if not callable(func):
        raise TypeError(
            "@clauz3.trusted takes no arguments: a function either is the "
            "trusted boundary or it is not. Effect markers belong to deal "
            f"(@deal.has{(func,)!r}) and contracts name the function itself."
        )
    return func
