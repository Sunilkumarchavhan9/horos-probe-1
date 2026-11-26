"""Asymmetric Ed25519 digital signature signing and verification for blind holdout manifests.

Implements Stage 3.7A Workstream B:
- Asymmetric cryptography: Dataset Custodian signs with Ed25519 private key; Evaluator verifies with Ed25519 public key.
- Canonical JSON serialization (RFC 8785 style: sorted keys, compact separators).
- Signed envelope schema: {payload, signature, algorithm, key_id, timestamp}.
- Key versioning and revocation support (REVOKED_ED25519_KEY_IDS).
- Rejection of malformed signatures, altered payloads, untrusted keys, and algorithm confusion.
- Explicit HMAC test utilities clearly differentiated from Ed25519 production signatures.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
from typing import Any, Dict, FrozenSet, Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from .contracts import ContractViolationError, ManifestSignatureError


# List of revoked Ed25519 key identifiers
REVOKED_ED25519_KEY_IDS: FrozenSet[str] = frozenset({
    "HOROS-REVOKED-TEST-KEY-000",
})


def canonicalize_manifest_bytes(manifest_data: Dict[str, Any]) -> bytes:
    """Produces deterministic canonical UTF-8 bytes for a JSON object."""
    return json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate_ed25519_keypair() -> Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Generates an ephemeral Ed25519 keypair (for test fixture and custodian initialization only)."""
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv, pub


def sign_manifest_ed25519(
    manifest_data: Dict[str, Any],
    private_key: ed25519.Ed25519PrivateKey,
    key_id: str = "HOROS-EVAL-KEY-V1",
) -> Dict[str, Any]:
    """Signs a manifest dictionary with an Ed25519 private key, returning a signed envelope."""
    if not isinstance(manifest_data, dict):
        raise ContractViolationError("Manifest data to sign must be a dictionary.")
    if not isinstance(private_key, ed25519.Ed25519PrivateKey):
        raise ContractViolationError("Signing requires a valid ed25519.Ed25519PrivateKey instance.")

    canonical_bytes = canonicalize_manifest_bytes(manifest_data)
    raw_signature = private_key.sign(canonical_bytes)
    sig_hex = raw_signature.hex()

    envelope = {
        "algorithm": "Ed25519",
        "key_id": str(key_id),
        "signature": sig_hex,
        "signed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "payload": manifest_data,
    }
    return envelope


def verify_manifest_ed25519(
    signed_envelope: Dict[str, Any],
    public_key: Union[ed25519.Ed25519PublicKey, bytes, str],
    expected_key_id: Optional[str] = None,
) -> bool:
    """Verifies an Ed25519 signed manifest envelope against a trusted public key.
    
    Fails closed (returns False or raises ManifestSignatureError) on any discrepancy.
    """
    if not isinstance(signed_envelope, dict):
        raise ManifestSignatureError("Signed envelope must be a JSON object dictionary.")

    required_keys = {"algorithm", "key_id", "signature", "payload"}
    if not required_keys.issubset(signed_envelope.keys()):
        raise ManifestSignatureError(f"Signed envelope missing required keys. Expected {required_keys}.")

    # Algorithm check: strictly reject algorithm substitution (e.g. HMAC or RSA where Ed25519 expected)
    algo = signed_envelope["algorithm"]
    if algo != "Ed25519":
        raise ManifestSignatureError(f"Unsupported signature algorithm '{algo}'. Expected 'Ed25519'.")

    # Key revocation check
    key_id = signed_envelope["key_id"]
    if key_id in REVOKED_ED25519_KEY_IDS:
        raise ManifestSignatureError(f"Key identifier '{key_id}' has been formally revoked.")

    if expected_key_id and key_id != expected_key_id:
        raise ManifestSignatureError(f"Key ID mismatch: envelope has '{key_id}', expected '{expected_key_id}'.")

    # Load public key
    if isinstance(public_key, str):
        try:
            pub_bytes = bytes.fromhex(public_key)
            pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        except Exception as exc:
            raise ManifestSignatureError(f"Invalid public key hex string: {exc}") from exc
    elif isinstance(public_key, bytes):
        try:
            pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
        except Exception as exc:
            raise ManifestSignatureError(f"Invalid public key bytes: {exc}") from exc
    elif isinstance(public_key, ed25519.Ed25519PublicKey):
        pub_obj = public_key
    else:
        raise ManifestSignatureError(f"Unsupported public_key type: {type(public_key)}")

    # Parse signature bytes
    sig_hex = signed_envelope["signature"]
    try:
        sig_bytes = bytes.fromhex(sig_hex)
        if len(sig_bytes) != 64:
            return False
    except Exception:
        return False

    # Extract canonical bytes of payload
    payload = signed_envelope["payload"]
    if not isinstance(payload, dict):
        raise ManifestSignatureError("Envelope 'payload' must be a JSON dictionary.")
    canonical_bytes = canonicalize_manifest_bytes(payload)

    # Perform asymmetric cryptographic verification
    try:
        pub_obj.verify(sig_bytes, canonical_bytes)
        return True
    except InvalidSignature:
        return False
    except Exception as exc:
        raise ManifestSignatureError(f"Cryptographic verification error: {exc}") from exc


# ---------------------------------------------------------------------------
# Explicit HMAC Test Utilities (Renamed and Isolated for Unit Testing Only)
# ---------------------------------------------------------------------------

def compute_hmac_test_manifest_signature(manifest_bytes: bytes, secret_key: bytes) -> str:
    """HMAC-SHA256 test utility. NOT equivalent to Ed25519 asymmetric signatures."""
    return hmac.new(secret_key, manifest_bytes, hashlib.sha256).hexdigest()


def verify_hmac_test_manifest_signature(
    manifest_bytes: bytes,
    signature_hex: str,
    verification_key: bytes,
) -> bool:
    """HMAC-SHA256 test verification helper. NOT for production Ed25519 manifests."""
    try:
        expected = compute_hmac_test_manifest_signature(manifest_bytes, verification_key)
        return hmac.compare_digest(expected.lower(), signature_hex.lower())
    except Exception:
        return False
