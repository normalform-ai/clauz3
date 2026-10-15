"""Compatibility imports; prefer :mod:`clauz3.approvals.authority`."""

from clauz3.approvals.authority import (
    ApprovalAuthorizationError,
    ApprovedLayer,
    authorize_layer,
)

__all__ = [
    "ApprovalAuthorizationError",
    "ApprovedLayer",
    "authorize_layer",
]
