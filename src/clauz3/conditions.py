"""Preconditions and postconditions on a trusted function.

These are not documentation and not a runtime nicety: they are the two things
a proof knows about a trusted function's *values*, since its body is never
evaluated.

:func:`requires` is an obligation at every call site. The prover must discharge
it from what it knows about the arguments, so ``send_email("not-an-email")``
is rejected before anything runs rather than raising when it runs.

:func:`ensures` is the only thing that narrows what comes *back*. A trusted
return is materialized as a fresh unconstrained symbol of the annotated sort,
because the body that would have computed it is skipped; ``ensures(lambda
result: len(result) <= 100)`` is what makes a query's result bounded enough to
iterate over, and ``ensures(lambda cap, result: result <= cap)`` is how a
result that is only meaningful relative to an argument says so.

Both were spelled ``@deal.pre`` / ``@deal.post`` until this module existed, and
were read by deal's extractor, which matches the literal source text
``deal.pre`` (``SUPPORTED_CONTRACTS`` in
``deal/linter/_extractors/contracts.py``). A trusted layer that wrote ``import
deal as d`` therefore had its preconditions silently dropped --- not
downgraded to a runtime check, dropped from the proof --- so a program passing
exactly the argument the precondition forbids reported ``proved!``. Resolving
the decorator through the module's import bindings, the way
:mod:`clauz3.decorators` resolves a guarantee, is what makes the spelling stop
deciding. (The same resolution now also rescues an aliased ``@d.pre``; see
:func:`clauz3.prover._clauz3_contracts`.)

Runtime enforcement is delegated to deal, unchanged: a violated precondition
raises ``PreContractError`` at the boundary exactly as before. clauz3
discharges the obligation ahead of execution and deal remains the backstop
when a proof is skipped or a contract is deliberately weak --- so what changed
is only that a trusted layer no longer has to import deal to state what its
functions require.
"""

import inspect
from collections.abc import Callable
from typing import TypeVar

import deal

F = TypeVar("F", bound=Callable[..., object])


def requires(
    predicate: Callable[..., object],
    *,
    message: str | None = None,
    exception: type[Exception] | Exception | None = None,
) -> Callable[[F], F]:
    """Require ``predicate`` of the arguments at every call.

    ``predicate`` is a lambda over the function's own parameters, written in
    the proved subset (see ``docs/reference/python-subset.md``)::

        @clauz3.requires(lambda addr, msg: "@" in addr, message='addr needs "@"')
        @clauz3.trusted
        def send_email(addr: str, msg: str) -> None: ...

    A caller that cannot be shown to satisfy it fails the proof as an
    *assertion*, distinct from a failed guarantee: the program is not wrong
    about what it promised, it is wrong about what it may call.

    ``message`` and ``exception`` customise the runtime failure and are passed
    through to :func:`deal.pre`.
    """

    return deal.pre(predicate, message=message, exception=exception)


def ensures(
    predicate: Callable[..., object],
    *,
    message: str | None = None,
    exception: type[Exception] | Exception | None = None,
) -> Callable[[F], F]:
    """Promise ``predicate`` of the return value.

    With one parameter, ``predicate`` is a lambda over the result alone::

        @clauz3.ensures(lambda result: len(result) <= 100)
        @clauz3.trusted
        def read_rows(table: str) -> list[Row]: ...

    With more, it also reads the function's own parameters, and one of them
    must be named ``result``::

        @clauz3.ensures(lambda cap, result: result <= cap)
        @clauz3.trusted
        def count_up_to(cap: int) -> int: ...

    The second form is the one a proof cannot do without where a trusted
    function's result is only meaningful relative to what it was asked for. It
    is also the limit of what the expression subset can currently say about a
    return: a promise that *quantifies over a returned list* --- every row the
    query returns satisfies the ``where`` filter, every path the walk returns
    is under its root --- has no spelling yet, because ``all(... for x in
    result)`` is not in the subset. Such a claim stays what it is today: a
    docstring, believed because the trusted layer is audited.

    The prover *assumes* a postcondition where a caller uses the value, which
    is the opposite direction from :func:`requires`: a precondition is checked
    at the call site, a postcondition is trusted there. It is a claim about the
    trusted layer's own implementation, which the trusted layer is responsible
    for --- an unsound one makes proofs about its callers unsound too.

    ``message`` and ``exception`` are passed through to deal, which checks the
    claim at runtime.
    """

    parameters = list(inspect.signature(predicate).parameters)
    if len(parameters) <= 1:
        return deal.post(predicate, message=message, exception=exception)
    if "result" not in parameters:
        raise TypeError(
            "a @clauz3.ensures predicate over the arguments must take a "
            f"parameter named 'result'; got {parameters!r}. The prover binds "
            "the return value to that name, and so does the runtime check."
        )
    return deal.ensure(predicate, message=message, exception=exception)
