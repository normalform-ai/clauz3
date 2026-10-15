"""Compatibility imports; prefer :mod:`clauz3.approvals.service`."""

from clauz3.approvals.service import (
    APPROVER_SESSION_COOKIE,
    DEFAULT_MAX_WAIT_SECONDS,
    create_approval_app,
    serve_approval_service,
)

__all__ = [
    "APPROVER_SESSION_COOKIE",
    "DEFAULT_MAX_WAIT_SECONDS",
    "create_approval_app",
    "serve_approval_service",
]
