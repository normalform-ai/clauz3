"""Existing embedding imports share the relocated classes and implementations."""

from clauz3 import approval, approval_authority, approval_policy, approval_service
from clauz3.approvals import authority, client, policy, service
from clauz3.libraries import Library, catalog, discovery


def test_approval_embedding_imports_remain_compatible() -> None:
    assert approval.ApprovalResponse is client.ApprovalResponse
    assert approval.submit_approval_request is client.submit_approval_request
    assert approval_authority.ApprovedLayer is authority.ApprovedLayer
    assert (
        approval_authority.ApprovalAuthorizationError
        is authority.ApprovalAuthorizationError
    )
    assert approval_policy.ApprovalPolicy is policy.ApprovalPolicy
    assert approval_policy.load_approval_policy is policy.load_approval_policy
    assert approval_service.create_approval_app is service.create_approval_app
    assert approval_service.serve_approval_service is service.serve_approval_service


def test_library_discovery_imports_remain_compatible() -> None:
    assert Library is discovery.Library
    assert catalog is discovery.catalog
