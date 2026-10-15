"""Verifiable approval receipts (Ed25519, fail-closed).

An approval receipt is the token the runner checks before executing an approved
program. It must:

- bind the approval to a specific ``request_id`` and ``program_sha256`` so an
  approval for program A cannot serve for program B;
- bind to the exact guarantees + coverage the human consented to
  (``consent_sha256``), so consent under one set of contracts cannot be reused
  under another;
- be **unforgeable by the runtime**, which is why signing is *asymmetric*: the
  approval service holds the Ed25519 *private* key and the runner holds only the
  *public* verifying key. The approved program is ``exec``-ed in the runner's
  process, so anything reachable from that process must be unable to mint a
  receipt — a symmetric/HMAC secret co-located with the agent would not be.

Format: ``clz2.<payload>.<sig>`` where ``payload`` is url-safe-base64 of canonical
JSON ``{request_id, program_sha256, decision, expires_at, nonce, consent_sha256}``
and ``sig`` is the url-safe-base64 Ed25519 signature over the ``payload`` segment.

Keys are provisioned out-of-band via the environment (see :func:`signing_key`
and :func:`verifying_key`); the agent must not be able to set them. Verification
is **fail-closed**: the runner refuses to run when no verifying key is provided.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections.abc import Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

#: Decisions that authorize execution. Kept local to avoid importing the
#: approval module (which imports networking machinery) into the runner's
#: verification path.
APPROVED_DECISIONS = frozenset({"approved_once", "approved_remember", "auto_approved"})

#: Service-side env var holding the base64 Ed25519 *private* signing key.
SIGNING_KEY_ENV = "CLAUZ3_APPROVAL_SIGNING_KEY"
#: Runner-side env var holding the base64 Ed25519 *public* verifying key. This
#: is the trust anchor: it must be provisioned by the operator, never the agent.
VERIFY_KEY_ENV = "CLAUZ3_APPROVAL_VERIFY_KEY"

_PREFIX = "clz2"


class ReceiptError(Exception):
    """A receipt is malformed, forged, expired, replayed, or bound to another run."""


def generate_keypair() -> tuple[str, str]:
    """Return ``(signing_key_b64, verify_key_b64)`` for a fresh Ed25519 keypair."""

    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return _b64encode(private_raw), _b64encode(public_raw)


def signing_key() -> str | None:
    """The service's base64 Ed25519 private signing key, or ``None``."""

    return os.environ.get(SIGNING_KEY_ENV) or None


def verifying_key() -> str | None:
    """The runner's base64 Ed25519 public verifying key, or ``None``."""

    return os.environ.get(VERIFY_KEY_ENV) or None


def consent_digest(
    *,
    program_sha256: str,
    guarantees: Sequence[str],
    coverage: object,
) -> str:
    """Hash the human-visible consent surface: program, guarantees, coverage.

    Folded into the signed receipt so an approval granted under one set of
    proved contracts cannot be replayed for a program with different ones.
    """

    payload = json.dumps(
        {
            "program_sha256": program_sha256,
            "guarantees": list(guarantees),
            "coverage": coverage,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def sign_receipt(
    *,
    signing_key_b64: str,
    request_id: str,
    program_sha256: str,
    decision: str,
    expires_at: float,
    nonce: str,
    consent_sha256: str,
) -> str:
    """Mint a signed receipt binding an approval to a program, request, consent."""

    payload = {
        "request_id": request_id,
        "program_sha256": program_sha256,
        "decision": decision,
        "expires_at": expires_at,
        "nonce": nonce,
        "consent_sha256": consent_sha256,
    }
    segment = _b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    private = _load_private_key(signing_key_b64)
    signature = private.sign(segment.encode())
    return f"{_PREFIX}.{segment}.{_b64encode(signature)}"


def verify_receipt(
    *,
    verify_key_b64: str,
    receipt: str,
    request_id: str,
    program_sha256: str,
    consent_sha256: str,
    now: float,
) -> str:
    """Verify a receipt for this run, returning its nonce; raise on any failure.

    Checks: well-formedness, Ed25519 signature, binding to *this* ``request_id``
    / ``program_sha256`` / ``consent_sha256``, an authorizing decision, and
    expiry. The returned nonce lets the caller enforce single-use.
    """

    parts = receipt.split(".")
    if len(parts) != 3 or parts[0] != _PREFIX:
        raise ReceiptError("malformed receipt")
    _, segment, signature_b64 = parts

    public = _load_public_key(verify_key_b64)
    try:
        public.verify(_b64decode(signature_b64), segment.encode())
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise ReceiptError("receipt signature did not verify") from exc

    payload = _decode_payload(segment)
    if payload.get("request_id") != request_id:
        raise ReceiptError("receipt is not bound to this request id")
    if payload.get("program_sha256") != program_sha256:
        raise ReceiptError("receipt is not bound to this program")
    if payload.get("consent_sha256") != consent_sha256:
        raise ReceiptError("receipt is not bound to these guarantees/coverage")
    if payload.get("decision") not in APPROVED_DECISIONS:
        raise ReceiptError("receipt does not authorize execution")
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, (int, float)) or now > expires_at:
        raise ReceiptError("receipt has expired")
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise ReceiptError("receipt is missing a nonce")
    return nonce


def _load_private_key(signing_key_b64: str) -> Ed25519PrivateKey:
    try:
        return Ed25519PrivateKey.from_private_bytes(_b64decode(signing_key_b64))
    except (binascii.Error, ValueError) as exc:
        raise ReceiptError("invalid signing key") from exc


def _load_public_key(verify_key_b64: str) -> Ed25519PublicKey:
    try:
        return Ed25519PublicKey.from_public_bytes(_b64decode(verify_key_b64))
    except (binascii.Error, ValueError) as exc:
        raise ReceiptError("invalid verifying key") from exc


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _decode_payload(segment: str) -> dict[str, object]:
    try:
        raw = _b64decode(segment)
        payload = json.loads(raw.decode())
    except (binascii.Error, ValueError) as exc:
        raise ReceiptError("receipt payload is not decodable") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("receipt payload is not an object")
    return payload
