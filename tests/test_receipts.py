"""Issue #69: approval receipts must be asymmetrically signed and bound.

A receipt binds an approval to a specific program, request, and consent surface
(guarantees + coverage), and is signed with an Ed25519 *private* key the service
holds. The runner verifies with only the *public* key, so nothing reachable from
the runner's process (where the approved program executes) can forge one.
"""

from __future__ import annotations

import pytest

from clauz3.approvals.receipts import (
    ReceiptError,
    consent_digest,
    generate_keypair,
    sign_receipt,
    verify_receipt,
)

SHA = "a" * 64
RID = "clr_abc123abc123"
NOW = 1_000_000.0
CONSENT = consent_digest(
    program_sha256=SHA, guarantees=["emails.only(['bob@b.c'])"], coverage=[]
)


def _make(signing: str, **overrides: object) -> str:
    kwargs: dict[str, object] = {
        "signing_key_b64": signing,
        "request_id": RID,
        "program_sha256": SHA,
        "decision": "approved_once",
        "expires_at": NOW + 300,
        "nonce": "nonce-1",
        "consent_sha256": CONSENT,
    }
    kwargs.update(overrides)
    return sign_receipt(**kwargs)  # type: ignore[arg-type]


def test_valid_receipt_verifies_and_returns_nonce() -> None:
    signing, verify = generate_keypair()
    nonce = verify_receipt(
        verify_key_b64=verify,
        receipt=_make(signing),
        request_id=RID,
        program_sha256=SHA,
        consent_sha256=CONSENT,
        now=NOW,
    )
    assert nonce == "nonce-1"


def test_wrong_key_rejected() -> None:
    signing, _ = generate_keypair()
    _, other_verify = generate_keypair()  # a different keypair's public key
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=other_verify,
            receipt=_make(signing),
            request_id=RID,
            program_sha256=SHA,
            consent_sha256=CONSENT,
            now=NOW,
        )


def test_tampered_payload_rejected() -> None:
    signing, verify = generate_keypair()
    prefix, payload, sig = _make(signing).split(".")
    # Flip a payload character: the signed message changes, so the signature
    # (still valid for the original payload) no longer verifies.
    flipped = ("A" if payload[0] != "A" else "B") + payload[1:]
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=f"{prefix}.{flipped}.{sig}",
            request_id=RID,
            program_sha256=SHA,
            consent_sha256=CONSENT,
            now=NOW,
        )


def test_program_hash_binding_enforced() -> None:
    signing, verify = generate_keypair()
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=_make(signing),
            request_id=RID,
            program_sha256="b" * 64,
            consent_sha256=CONSENT,
            now=NOW,
        )


def test_request_id_binding_enforced() -> None:
    signing, verify = generate_keypair()
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=_make(signing),
            request_id="clr_different000",
            program_sha256=SHA,
            consent_sha256=CONSENT,
            now=NOW,
        )


def test_consent_binding_enforced() -> None:
    signing, verify = generate_keypair()
    other_consent = consent_digest(
        program_sha256=SHA, guarantees=["emails.unique_recipients()"], coverage=[]
    )
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=_make(signing),
            request_id=RID,
            program_sha256=SHA,
            consent_sha256=other_consent,
            now=NOW,
        )


def test_expired_receipt_rejected() -> None:
    signing, verify = generate_keypair()
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=_make(signing, expires_at=NOW - 1),
            request_id=RID,
            program_sha256=SHA,
            consent_sha256=CONSENT,
            now=NOW,
        )


def test_non_approval_decision_rejected() -> None:
    signing, verify = generate_keypair()
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=_make(signing, decision="rejected"),
            request_id=RID,
            program_sha256=SHA,
            consent_sha256=CONSENT,
            now=NOW,
        )


@pytest.mark.parametrize("bad", ["", "garbage", "clz2.only-two", "clz1.a.b"])
def test_malformed_receipt_rejected(bad: str) -> None:
    _, verify = generate_keypair()
    with pytest.raises(ReceiptError):
        verify_receipt(
            verify_key_b64=verify,
            receipt=bad,
            request_id=RID,
            program_sha256=SHA,
            consent_sha256=CONSENT,
            now=NOW,
        )
