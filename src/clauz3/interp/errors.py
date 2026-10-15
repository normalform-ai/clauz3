"""Errors raised by the subset interpreter."""

from __future__ import annotations


class InterpError(Exception):
    """Base class for every interpreter failure."""


class UnsupportedNode(InterpError):
    """The program used a construct outside the proved subset.

    This is the interpreter's single fail-closed exit. Anything the tree-walk
    does not have an explicit handler for lands here, so a construct that the
    prover cannot reason about also cannot execute.
    """

    def __init__(self, *parts: str) -> None:
        super().__init__(" ".join(str(part) for part in parts))


class NameResolutionError(InterpError):
    """A name did not resolve to anything the subset can name."""


class TrustedBoundaryError(InterpError):
    """A value crossing the trusted boundary is outside the value domain.

    Trusted layers are ordinary Python and may compute with anything, but the
    values they hand back to an interpreted program must be subset data. A
    trusted function that returns a live Python object would otherwise donate
    its whole object graph -- and CPython's reflection surface -- to untrusted
    code.
    """


class ProgramFault(InterpError):
    """The program ran as written and failed, rather than leaving the subset.

    Split out from the fail-closed errors above because callers must not
    conflate the two. ``UnsupportedNode`` and friends say "this source should
    never have been submitted" -- a configuration fault, ``clauz3 run`` exit 2.
    A ``ProgramFault`` says "a proved, approved program raised at run time",
    which under the old ``exec`` path surfaced as an ordinary Python exception
    and exit 1. Wrapping every ``InterpError`` as a configuration error
    reclassified every runtime fault as a bad-source error.
    """


class ExecutionLimitExceeded(ProgramFault):
    """The program exceeded the interpreter's step budget."""


class ProgramError(ProgramFault):
    """The interpreted program raised or failed an assertion.

    This is a *program* fault, not an interpreter fault: the program ran
    exactly as written and reached a ``raise`` or a failing ``assert``.
    """

    def __init__(self, message: str, *, kind: str = "Exception") -> None:
        # The rendered form carries the kind. ``raise ValueError('boom')``
        # reported only ``'boom'`` before, which reads as an interpreter
        # complaint rather than the program's own exception once the runner
        # prefixes it with the source name.
        detail = f"{kind}: {message}" if message and message != kind else kind
        super().__init__(detail)
        self.message = message
        self.kind = kind
