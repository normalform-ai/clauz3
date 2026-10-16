"""The closed vocabulary of approval decisions.

Every decision the approval protocol carries is one of the exact strings below:
a click in the bundled UI, the body of ``POST /requests/{id}/decision``, a
policy rule, a remembered profile rule, a mock config, the ``decision`` field of
a service response or of a signed receipt. Anything else is a protocol error
and is refused where it arrives. It is never quietly read as a denial, or as an
approval.

Issue #62 came from an integrator posting ``"approved"``. The service accepted
it, the runner saw a string outside the approved set and denied the run, and
the transcript read ``approval: approved``.

This module imports nothing else from clauz3, so receipt verification on the
runner's path can use it without pulling in networking code.

>>> Decision("approved_once") is Decision.APPROVED_ONCE
True
>>> "approved_once" in APPROVED_DECISIONS
True
>>> sorted(AUTO_DECISIONS)
[<Decision.AUTO_APPROVED: 'auto_approved'>, <Decision.AUTO_REJECTED: 'auto_rejected'>]
"""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum


class Decision(StrEnum):
    """A decision recorded for an approval request, spelled as on the wire."""

    APPROVED_ONCE = "approved_once"
    APPROVED_REMEMBER = "approved_remember"
    AUTO_APPROVED = "auto_approved"
    REJECTED = "rejected"
    REJECTED_CONTRACT = "rejected_contract"
    REQUEST_MORE = "request_more"
    AUTO_REJECTED = "auto_rejected"

    @property
    def approved(self) -> bool:
        """Whether this decision authorizes execution.

        >>> Decision.APPROVED_ONCE.approved, Decision.REQUEST_MORE.approved
        (True, False)
        """

        return self in APPROVED_DECISIONS


#: Decisions that authorize execution; every other decision is a denial.
APPROVED_DECISIONS = frozenset(
    {Decision.APPROVED_ONCE, Decision.APPROVED_REMEMBER, Decision.AUTO_APPROVED}
)
#: Decisions only a policy rule or a remembered profile rule issues.
AUTO_DECISIONS = frozenset({Decision.AUTO_APPROVED, Decision.AUTO_REJECTED})
#: Decisions a user may record through the UI or the decision API.
USER_DECISIONS = frozenset(Decision) - AUTO_DECISIONS
#: Human decisions a profile may remember as an allow or deny rule.
REMEMBERABLE_DECISIONS = frozenset(
    {
        Decision.APPROVED_ONCE,
        Decision.APPROVED_REMEMBER,
        Decision.REJECTED,
        Decision.REJECTED_CONTRACT,
    }
)


class UnknownDecisionError(ValueError):
    """A decision is not one of the allowed exact strings.

    >>> str(UnknownDecisionError("approved", AUTO_DECISIONS))
    "decision 'approved' is not one of ['auto_approved', 'auto_rejected']"
    """

    def __init__(self, value: object, allowed: Collection[Decision]) -> None:
        expected = sorted(decision.value for decision in allowed)
        super().__init__(f"decision {value!r} is not one of {expected}")
        self.value = value


def parse_decision(
    value: object, *, allowed: Collection[Decision] = frozenset(Decision)
) -> Decision:
    """Return the decision that ``value`` spells exactly, or raise.

    Matching is exact and case-sensitive, so ``"approved"``, ``"Approved_Once"``,
    ``""`` and ``None`` are all unknown. Pass ``allowed`` to narrow the set a
    boundary accepts; the decision API, for example, takes only
    :data:`USER_DECISIONS`.

    >>> parse_decision("approved_once")
    <Decision.APPROVED_ONCE: 'approved_once'>
    >>> parse_decision("approved")  # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    clauz3.approvals.decisions.UnknownDecisionError: decision 'approved' is not ...
    >>> parse_decision("auto_approved", allowed=USER_DECISIONS)  # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    clauz3.approvals.decisions.UnknownDecisionError: decision 'auto_approved' is ...
    """

    if isinstance(value, str) and value in allowed:
        return Decision(value)
    raise UnknownDecisionError(value, allowed)


def is_approved(value: object) -> bool:
    """Whether ``value`` is exactly an approving decision.

    Unknown values, including stored strings from before the vocabulary was
    closed, are never approvals.

    >>> is_approved("auto_approved"), is_approved("approved"), is_approved(None)
    (True, False, False)
    >>> is_approved(["approved_once"])
    False
    """

    return isinstance(value, str) and value in APPROVED_DECISIONS
