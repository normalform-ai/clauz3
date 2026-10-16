"""Compatibility imports; prefer :mod:`clauz3.approvals.policy`."""

from clauz3.approvals.decisions import AUTO_DECISIONS
from clauz3.approvals.policy import (
    ApprovalPolicy,
    ApprovalPolicyError,
    PolicyDecision,
    Rule,
    approval_policy_from_dict,
    auto_approval_response,
    evaluate_policy,
    load_approval_policy,
)

__all__ = [
    "AUTO_DECISIONS",
    "ApprovalPolicy",
    "ApprovalPolicyError",
    "PolicyDecision",
    "Rule",
    "approval_policy_from_dict",
    "auto_approval_response",
    "evaluate_policy",
    "load_approval_policy",
]
