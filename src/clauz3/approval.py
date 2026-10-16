"""Compatibility imports; prefer :mod:`clauz3.approvals.client`."""

from clauz3.approvals.client import (
    APPROVAL_CONFIG,
    APPROVAL_DECISION_TOKEN_ENV,
    APPROVAL_SERVICE_ENV,
    APPROVAL_TOKEN_ENV,
    APPROVAL_URL_ENV,
    DEFAULT_APPROVAL_TIMEOUT,
    DEFAULT_RECEIPT_TTL,
    ApprovalResponse,
    ApprovalServiceError,
    MockApprovalServer,
    approval_response_from_config,
    configured_service_url,
    load_mock_config,
    request_id_from_payload,
    serve_mock_approval_service,
    sign_approval_receipt,
    submit_approval_request,
)
from clauz3.approvals.decisions import APPROVED_DECISIONS

__all__ = [
    "APPROVAL_CONFIG",
    "APPROVAL_DECISION_TOKEN_ENV",
    "APPROVAL_SERVICE_ENV",
    "APPROVAL_TOKEN_ENV",
    "APPROVAL_URL_ENV",
    "APPROVED_DECISIONS",
    "ApprovalResponse",
    "ApprovalServiceError",
    "DEFAULT_APPROVAL_TIMEOUT",
    "DEFAULT_RECEIPT_TTL",
    "MockApprovalServer",
    "approval_response_from_config",
    "configured_service_url",
    "load_mock_config",
    "request_id_from_payload",
    "serve_mock_approval_service",
    "sign_approval_receipt",
    "submit_approval_request",
]
