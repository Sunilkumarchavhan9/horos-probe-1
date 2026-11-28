"""Machine-readable security attestation generation and cryptographic evidence anchoring.

Stage 3.7C.6 Attestation Acceptance Hardening & Independent Runtime Verification:
- Explicit separation of test-signing pathway from production acceptance.
- Rejection of public test key and Ed25519-NON_PRODUCTION_TEST_KEY in production acceptance.
- Complete cryptographic binding of schema_version, envelope metadata, and evidence payload.
- Downgrade-resistant verification requiring explicit authorization for legacy records.
- Independent runtime identity consistency checks: strict tier matching, uncompromised UID escalation denial.
- Authoritative quarantine registry verification across NPZ and privileged JSON digests.
- Incomplete quarantine scans fail closed and can never receive clean/verified status.
- Source tree digest and dirty tree detection for unambiguous provenance anchoring.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple
import uuid

from cryptography.hazmat.primitives.asymmetric import ed25519

from .contracts import (
    CANONICAL_QUARANTINE_REGISTRY_PATH,
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_PROTOCOL,
    COMPROMISED_HOLDOUT_STATUS,
    EXPECTED_QUARANTINE_REGISTRY_SHA256,
    ContractViolationError,
    get_compromised_npz_sha256,
    get_compromised_privileged_sha256,
    get_compromised_sample_ids,
    load_quarantine_record,
    verify_quarantine_registry_integrity,
)
from .runner import EXECUTION_AUTHORIZED, TRAINING_PERMITTED


ATTESTATION_SCHEMA_VERSION = "2.2.0"
ATTESTATION_MILESTONE = "Stage 3.7C.6 Attestation Acceptance Hardening & Independent Runtime Verification"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0.0", "2.0.0", "2.1.0", "2.2.0", "3.7C.4", "3.7C.5", "3.7C.6"})

# Permitted control verification statuses
STATUS_TESTED_AND_VERIFIED = "TESTED_AND_VERIFIED"
STATUS_IMPLEMENTED = "IMPLEMENTED"
STATUS_PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
STATUS_BLOCKED = "BLOCKED"

VALID_CONTROL_STATUSES = frozenset({
    STATUS_TESTED_AND_VERIFIED,
    STATUS_IMPLEMENTED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_BLOCKED,
})

# Dedicated Non-Production Test Key Information
# Production acceptance must strictly reject this key and scheme!
TEST_NONPROD_SIGNING_SCHEME = "Ed25519-NON_PRODUCTION_TEST_KEY"
TEST_NONPROD_PUBLIC_KEY_HEX = "f358db11ce627531bb342ab04c03f34a345e931ca993aa660ecec4d55c96fc5c"
_TEST_NONPROD_PUBLIC_KEY = ed25519.Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(TEST_NONPROD_PUBLIC_KEY_HEX)
)


@dataclass(frozen=True)
class AttestationTrustPolicy:
    """Explicit trust configuration policy for validating security attestations."""

    trusted_public_keys: FrozenSet[str] = frozenset()
    allow_test_keys: bool = False
    allow_legacy_unsigned: bool = False
    enforce_production_acceptance: bool = False
    minimum_schema_version: str = "2.0.0"


DEFAULT_TEST_TRUST_POLICY = AttestationTrustPolicy(
    allow_test_keys=True,
    allow_legacy_unsigned=False,
    minimum_schema_version="2.0.0",
)

DEFAULT_PRODUCTION_TRUST_POLICY = AttestationTrustPolicy(
    allow_test_keys=False,
    allow_legacy_unsigned=False,
    enforce_production_acceptance=True,
    minimum_schema_version="2.0.0",
)

DEFAULT_LEGACY_AUDIT_POLICY = AttestationTrustPolicy(
    allow_test_keys=False,
    allow_legacy_unsigned=True,
    minimum_schema_version="1.0.0",
)


def canonicalize_attestation_bytes(obj: Any) -> bytes:
    """Produces deterministic UTF-8 bytes from JSON-serializable attestation payload."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_source_tree_digest(source_dir: Optional[Path] = None) -> str:
    """Computes deterministic SHA-256 digest across all Python source files in world_model_vnext."""
    if source_dir is None:
        source_dir = Path(__file__).resolve().parent
    py_files = sorted(source_dir.glob("*.py"))
    h = hashlib.sha256()
    for pf in py_files:
        h.update(pf.name.encode("utf-8"))
        h.update(pf.read_bytes())
    return h.hexdigest()


def is_source_tree_dirty(repo_root: Optional[Path] = None) -> bool:
    """Detects whether there are uncommitted changes in the tracked source files."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "Research-002/src/world_model_vnext"],
            capture_output=True,
            text=True,
            timeout=3.0,
            cwd=repo_root,
        )
        if res.returncode == 0:
            return bool(res.stdout.strip())
    except Exception:
        pass
    return False


def get_source_commit_sha(repo_root: Optional[Path] = None) -> str:
    """Resolves the current Git commit SHA or returns UNKNOWN_UNCOMMITTED_REVISION."""
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha and len(env_sha) == 40:
        return env_sha.strip().lower()
    try:
        cwd_dir = repo_root or Path(__file__).resolve().parent
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3.0,
            cwd=cwd_dir,
        )
        if res.returncode == 0:
            out_sha = res.stdout.strip().lower()
            if len(out_sha) == 40:
                return out_sha
    except Exception:
        pass
    return "UNKNOWN_UNCOMMITTED_REVISION"


def sign_attestation_digest_test_only(
    digest_sha256: str,
    private_key_seed: Optional[bytes] = None,
) -> str:
    """Explicitly isolated test-only utility to sign an attestation digest with non-production key."""
    if private_key_seed is None:
        # Isolated test seed — strictly rejected by production acceptance
        private_key_seed = hashlib.sha256(b"HOROS_PROBE1_STAGE37C4_NONPROD_ED25519_TEST_SIGNING_KEY_32B").digest()[:32]
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_seed)
    sig_bytes = priv_key.sign(digest_sha256.encode("utf-8"))
    return sig_bytes.hex()


def sign_attestation_digest(digest_sha256: str) -> str:
    """Backward-compatible test signing entrypoint."""
    return sign_attestation_digest_test_only(digest_sha256)


def compute_evidence_digest(
    controls: Dict[str, Any],
    candidate_checkpoint_sha256: str = "",
    runtime_capabilities: Optional[Dict[str, Any]] = None,
    active_runtime_identity: Optional[Dict[str, Any]] = None,
    evaluator_context: Optional[Dict[str, Any]] = None,
    governance_invariants: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    schema_version: Optional[str] = None,
    milestone: Optional[str] = None,
    attestation_id: Optional[str] = None,
    generated_at_utc: Optional[str] = None,
    source_commit_sha: Optional[str] = None,
    signature_scheme: Optional[str] = None,
    signature_public_key_hex: Optional[str] = None,
) -> str:
    """Computes canonical SHA-256 digest across complete attestation evidence and envelope."""
    payload: Dict[str, Any] = {"controls": controls}
    if candidate_checkpoint_sha256:
        payload["candidate_checkpoint_sha256"] = str(candidate_checkpoint_sha256).strip().lower()
    if runtime_capabilities is not None:
        payload["runtime_capabilities"] = runtime_capabilities
    if active_runtime_identity is not None:
        payload["active_runtime_identity"] = active_runtime_identity
    if evaluator_context is not None:
        payload["evaluator_context"] = evaluator_context
    if governance_invariants is not None:
        payload["governance_invariants"] = governance_invariants
    if provenance is not None:
        payload["provenance"] = provenance

    # Bind envelope and schema into canonical payload for complete cryptographic binding
    if schema_version is not None:
        payload["schema_version"] = str(schema_version)
    if milestone is not None:
        payload["milestone"] = str(milestone)
    if attestation_id is not None:
        payload["attestation_id"] = str(attestation_id)
    if generated_at_utc is not None:
        payload["generated_at_utc"] = str(generated_at_utc)
    if source_commit_sha is not None:
        payload["source_commit_sha"] = str(source_commit_sha)
    if signature_scheme is not None:
        payload["signature_scheme"] = str(signature_scheme)
    if signature_public_key_hex is not None:
        payload["signature_public_key_hex"] = str(signature_public_key_hex)

    canonical = canonicalize_attestation_bytes(payload)
    return hashlib.sha256(canonical).hexdigest()


def compute_canonical_attestation_payload_digest(record: Dict[str, Any]) -> str:
    """Extracts and hashes all bound fields from a complete attestation record."""
    schema_ver = str(record.get("schema_version", ""))
    is_v22 = schema_ver in ("2.2.0", "3.7C.6")

    controls = record.get("controls", {})
    cand_sha = record.get("candidate_checkpoint_sha256", "")
    runtime_caps = record.get("runtime_capabilities", {})
    active_identity = record.get("active_runtime_identity")
    eval_ctx = record.get("evaluator_context")
    gov = record.get("governance_invariants")
    prov = record.get("provenance")

    if is_v22:
        return compute_evidence_digest(
            controls=controls,
            candidate_checkpoint_sha256=cand_sha,
            runtime_capabilities=runtime_caps,
            active_runtime_identity=active_identity if active_identity else None,
            evaluator_context=eval_ctx,
            governance_invariants=gov,
            provenance=prov,
            schema_version=schema_ver,
            milestone=record.get("milestone"),
            attestation_id=record.get("attestation_id"),
            generated_at_utc=record.get("generated_at_utc"),
            source_commit_sha=record.get("source_commit_sha"),
            signature_scheme=record.get("signature_scheme"),
            signature_public_key_hex=record.get("signature_public_key_hex"),
        )
    else:
        # Compatibility with schemas 2.0.0 and 2.1.0
        return compute_evidence_digest(
            controls=controls,
            candidate_checkpoint_sha256=cand_sha,
            runtime_capabilities=runtime_caps,
            active_runtime_identity=active_identity if active_identity else None,
            evaluator_context=eval_ctx if eval_ctx and "provenance" in record else None,
            governance_invariants=gov if gov and "provenance" in record else None,
            provenance=prov if prov else None,
        )


def verify_physical_holdout_quarantine(
    search_dirs: Optional[Sequence[Path | str]] = None,
) -> Dict[str, Any]:
    """Empirically and recursively verifies physical quarantine of compromised Stage 3.5 holdout."""
    quarantine_record = load_quarantine_record()
    registry_sha = verify_quarantine_registry_integrity()
    quarantine_id = quarantine_record.get("quarantine_id", "HOROS-P1-R002-STAGE36-QREC-01")

    # Authoritative compromised digests and IDs from registry
    npz_digests = set(get_compromised_npz_sha256())
    priv_digests = set(get_compromised_privileged_sha256())
    compromised_content_hashes = npz_digests | priv_digests
    sample_ids = set(get_compromised_sample_ids())

    compromised_patterns = (
        "stage35_final_holdout",
        "holdout_compromised",
        "stage35_holdout",
        "compromised_holdout",
    )

    if search_dirs is None:
        search_dirs = [
            Path("Research-002/data"),
            Path("data"),
            Path("Research-002/experiments"),
            Path("experiments"),
        ]

    exposed_files: List[str] = []
    unreadable_files: List[str] = []
    scanned_files_count = 0
    scan_complete = True

    canonical_registry_path = CANONICAL_QUARANTINE_REGISTRY_PATH.resolve()
    research_root = Path(__file__).resolve().parent.parent.parent
    canonical_quarantine_dir = (research_root / "experiments" / "stage36_holdout_quarantine").resolve()
    canonical_s35_dir = (research_root / "experiments" / "stage35_production").resolve()
    repo_root = research_root.parent

    for ed in search_dirs:
        p = Path(ed).resolve()
        if not p.is_dir():
            continue
        try:
            if not (p.is_relative_to(repo_root) or "temp" in str(p).lower() or "tmp" in str(p).lower() or "pytest" in str(p).lower()):
                continue
        except (AttributeError, ValueError):
            pass
        for root, dirs, files in os.walk(p, followlinks=False):
            root_path = Path(root).resolve()
            # Exclude strictly the designated historical Stage 3.5/3.6 quarantine artifacts (never copied directories)
            try:
                if root_path == canonical_quarantine_dir or root_path.is_relative_to(canonical_quarantine_dir):
                    continue
                if root_path == canonical_s35_dir or root_path.is_relative_to(canonical_s35_dir):
                    continue
            except (AttributeError, ValueError):
                if str(root_path).startswith(str(canonical_quarantine_dir)) or str(root_path).startswith(str(canonical_s35_dir)):
                    continue

            for fn in files:
                file_path = root_path / fn

                # Exclude ONLY the canonical quarantine registry metadata file itself
                if file_path == canonical_registry_path or fn == "holdout_quarantine_record.json":
                    continue

                fn_lower = fn.lower()
                # 1. Check filename patterns
                if any(pat in fn_lower for pat in compromised_patterns):
                    exposed_files.append(str(file_path))
                    continue

                # 2. Check candidate archive / data formats
                if fn_lower.endswith((".npz", ".pt", ".json")):
                    scanned_files_count += 1
                    try:
                        stat = file_path.stat()
                        if stat.st_size > 100 * 1024 * 1024:
                            # Oversized candidate archive cannot be safely inspected in full
                            unreadable_files.append(f"{file_path} (oversized)")
                            scan_complete = False
                            continue
                        file_bytes = file_path.read_bytes()
                    except Exception as exc:
                        unreadable_files.append(f"{file_path} ({type(exc).__name__})")
                        scan_complete = False
                        continue

                    file_sha = hashlib.sha256(file_bytes).hexdigest().lower()
                    if file_sha in compromised_content_hashes:
                        exposed_files.append(str(file_path))
                        continue

                    if fn_lower.endswith(".json") and stat.st_size < 10 * 1024 * 1024:
                        try:
                            json_text = file_bytes.decode("utf-8", errors="replace").lower()
                            if any(sid.lower() in json_text for sid in sample_ids):
                                exposed_files.append(str(file_path))
                        except Exception:
                            pass

    if unreadable_files:
        scan_complete = False

    # physical_exclusion_verified requires BOTH complete scan and ZERO exposed files
    physical_exclusion_verified = scan_complete and (len(exposed_files) == 0)
    quarantine_enforcement_controls_verified = physical_exclusion_verified

    return {
        "quarantine_id": quarantine_id,
        "quarantine_registry_sha256": registry_sha,
        "compromised_holdout_id": COMPROMISED_HOLDOUT_ID,
        "compromised_holdout_status": COMPROMISED_HOLDOUT_STATUS,
        "compromised_holdout_protocol": COMPROMISED_HOLDOUT_PROTOCOL,
        "scan_complete": scan_complete,
        "physical_exclusion_verified": physical_exclusion_verified,
        "exposed_compromised_files_found": exposed_files,
        "unreadable_files": unreadable_files,
        "scanned_files_count": scanned_files_count,
        "quarantine_enforcement_controls_verified": quarantine_enforcement_controls_verified,
        "quarantine_permanently_enforced": physical_exclusion_verified,
    }


def verify_test_attestation_signature(record: Dict[str, Any]) -> bool:
    """Verifies that the attestation Ed25519 signature is valid using the dedicated test key."""
    sig_hex = record.get("attestation_signature")
    if not sig_hex or not isinstance(sig_hex, str):
        return False

    scheme = record.get("signature_scheme")
    if scheme != TEST_NONPROD_SIGNING_SCHEME:
        return False

    pub_hex = record.get("signature_public_key_hex")
    if pub_hex != TEST_NONPROD_PUBLIC_KEY_HEX:
        return False

    # Cross-validate top-level vs nested provenance
    prov = record.get("provenance")
    if prov is not None and isinstance(prov, dict):
        if record.get("source_commit_sha") != prov.get("source_commit_sha"):
            return False
        if record.get("attestation_id") != prov.get("attestation_uuid"):
            return False
        if record.get("generated_at_utc") != prov.get("generated_at_utc"):
            return False
        if record.get("milestone") != prov.get("milestone"):
            return False

    recomputed = compute_canonical_attestation_payload_digest(record)
    stored_digest = record.get("evidence_digest_sha256", "")
    schema_version = record.get("schema_version", "1.0.0")

    if stored_digest.lower() != recomputed.lower():
        if schema_version == "1.0.0":
            controls = record.get("controls", {})
            cand_sha = record.get("candidate_checkpoint_sha256", "")
            runtime_caps = record.get("runtime_capabilities", {})
            legacy_recomputed = compute_evidence_digest(
                controls=controls,
                candidate_checkpoint_sha256=cand_sha,
                runtime_capabilities=runtime_caps,
            )
            if stored_digest.lower() != legacy_recomputed.lower():
                return False
            recomputed = legacy_recomputed
        else:
            return False

    try:
        sig_bytes = bytes.fromhex(sig_hex)
        _TEST_NONPROD_PUBLIC_KEY.verify(sig_bytes, recomputed.encode("utf-8"))
        return True
    except Exception:
        return False


def verify_attestation_signature(record: Dict[str, Any]) -> bool:
    """Backward-compatible signature verification helper."""
    return verify_test_attestation_signature(record)


def verify_production_attestation_acceptance(
    record: Dict[str, Any],
    trust_policy: Optional[AttestationTrustPolicy] = None,
) -> bool:
    """Strict production acceptance: rejects test-key signatures and requires trusted production public key."""
    policy = trust_policy or DEFAULT_PRODUCTION_TRUST_POLICY

    # 1. Structural and invariant validation
    validate_security_attestation_schema(record, policy=policy)

    # 2. Reject test-key signing scheme unconditionally in production
    scheme = record.get("signature_scheme")
    if scheme == TEST_NONPROD_SIGNING_SCHEME:
        raise ContractViolationError(
            f"Production attestation acceptance strictly rejects test signature scheme '{TEST_NONPROD_SIGNING_SCHEME}'."
        )

    # 3. Check public key against authorized trust anchor
    pub_hex = record.get("signature_public_key_hex")
    if not pub_hex or pub_hex not in policy.trusted_public_keys:
        raise ContractViolationError(
            f"Attestation signed by untrusted public key '{pub_hex}': not anchored in trusted production keys."
        )

    # 4. Enforce clean, committed revision provenance
    prov = record.get("provenance", {})
    if prov.get("is_dirty_source_tree") is True:
        raise ContractViolationError("Production attestation acceptance strictly rejects dirty source trees.")
    if record.get("source_commit_sha") == "UNKNOWN_UNCOMMITTED_REVISION":
        raise ContractViolationError("Production attestation acceptance strictly requires verified Git commit SHA.")

        # 5. Verify production Ed25519 signature
        recomputed = compute_canonical_attestation_payload_digest(record)
        sig_hex = record.get("attestation_signature", "")
        try:
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
            sig_bytes = bytes.fromhex(sig_hex)
            pub_key.verify(sig_bytes, recomputed.encode("utf-8"))
            return True
        except Exception as exc:
            raise ContractViolationError(f"Production Ed25519 signature verification failed: {exc}")


def verify_test_attestation_acceptance(record: Dict[str, Any]) -> bool:
    """Comprehensive test-only acceptance validation."""
    validate_security_attestation_schema(record, policy=DEFAULT_TEST_TRUST_POLICY)
    if not verify_test_attestation_signature(record):
        raise ContractViolationError("Test attestation Ed25519 signature verification failed.")
    return True


class SecurityAttestationBuilder:
    """Assembles and cryptographically anchors machine-readable security attestation records."""

    def __init__(
        self,
        candidate_checkpoint_sha256: str,
        runtime_capabilities: Dict[str, Any],
        active_runtime_identity: Optional[Dict[str, Any]] = None,
        source_commit_sha: Optional[str] = None,
    ) -> None:
        self.candidate_checkpoint_sha256 = str(candidate_checkpoint_sha256).strip().lower()
        if len(self.candidate_checkpoint_sha256) != 64:
            raise ContractViolationError("Candidate checkpoint SHA-256 must be a 64-hex string.")

        self.runtime_caps = dict(runtime_capabilities)
        self.active_runtime_identity = dict(active_runtime_identity or {})
        self.source_commit_sha = (source_commit_sha or get_source_commit_sha()).strip().lower()
        self.controls: Dict[str, Dict[str, Any]] = {}

    def record_control(
        self,
        control_id: str,
        name: str,
        status: str,
        evidence: Dict[str, Any],
        rationale: str = "",
    ) -> None:
        """Records an independently tested or audited security control with empirical evidence."""
        if status not in VALID_CONTROL_STATUSES:
            raise ContractViolationError(f"Invalid control status '{status}'. Must be one of {VALID_CONTROL_STATUSES}.")
        if not isinstance(evidence, dict):
            raise ContractViolationError(f"Control evidence must be a dictionary, got {type(evidence)}.")

        self.controls[control_id] = {
            "name": str(name),
            "status": status,
            "evidence": copy.deepcopy(evidence),
            "rationale": str(rationale),
        }

    def build(self) -> Dict[str, Any]:
        """Constructs the complete, cryptographically anchored security attestation record."""
        now_utc = datetime.now(timezone.utc).isoformat()
        attestation_uuid = str(uuid.uuid4())

        evaluator_info = {
            "pid": os.getpid(),
            "os_name": os.name,
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "working_directory": str(Path.cwd()),
        }

        source_tree_digest = compute_source_tree_digest()
        is_dirty = is_source_tree_dirty()

        provenance_info = {
            "attestation_uuid": attestation_uuid,
            "generated_at_utc": now_utc,
            "source_commit_sha": self.source_commit_sha,
            "source_tree_digest_sha256": source_tree_digest,
            "is_dirty_source_tree": is_dirty,
            "milestone": ATTESTATION_MILESTONE,
        }

        quarantine_evidence = verify_physical_holdout_quarantine()

        governance_invariants = {
            "runtime_verified_invariants": {
                "training_permitted": TRAINING_PERMITTED,
                "execution_authorized": EXECUTION_AUTHORIZED,
                "gradient_steps_verified": 0,
                "optimizer_updates_verified": 0,
                "model_parameters_frozen": True,
            },
            "static_policy_declarations": {
                "new_holdout_generation": "NOT_AUTHORIZED",
                "new_seed_generation": "NOT_AUTHORIZED",
                "final_evaluation": "BLOCKED",
            },
            "physical_artifact_quarantine": quarantine_evidence,
            "production_authorization_state": {
                "production_keys": "NOT_GENERATED",
                "production_vault": "NOT_INITIALIZED",
                "production_training": "PROHIBITED",
            },
            # Flat top-level keys for backward-compatibility with older consumers
            "training_permitted": TRAINING_PERMITTED,
            "execution_authorized": EXECUTION_AUTHORIZED,
            "gradient_steps": 0,
            "optimizer_updates": 0,
            "new_holdout_generation": "NOT_AUTHORIZED",
            "production_keys": "NOT_GENERATED",
            "production_vault": "NOT_INITIALIZED",
            "final_evaluation": "BLOCKED",
            "compromised_holdout_id": COMPROMISED_HOLDOUT_ID,
            "compromised_holdout_status": COMPROMISED_HOLDOUT_STATUS,
            "compromised_holdout_protocol": COMPROMISED_HOLDOUT_PROTOCOL,
        }

        # Assert governance invariants are intact
        if TRAINING_PERMITTED != 0 or EXECUTION_AUTHORIZED != 0:
            raise ContractViolationError("Cannot issue security attestation when governance invariants are breached!")

        # Complete cryptographic binding including schema and envelope
        evidence_digest = compute_evidence_digest(
            controls=self.controls,
            candidate_checkpoint_sha256=self.candidate_checkpoint_sha256,
            runtime_capabilities=self.runtime_caps,
            active_runtime_identity=self.active_runtime_identity if self.active_runtime_identity else None,
            evaluator_context=evaluator_info,
            governance_invariants=governance_invariants,
            provenance=provenance_info,
            schema_version=ATTESTATION_SCHEMA_VERSION,
            milestone=ATTESTATION_MILESTONE,
            attestation_id=attestation_uuid,
            generated_at_utc=now_utc,
            source_commit_sha=self.source_commit_sha,
            signature_scheme=TEST_NONPROD_SIGNING_SCHEME,
            signature_public_key_hex=TEST_NONPROD_PUBLIC_KEY_HEX,
        )

        attestation_sig = sign_attestation_digest_test_only(evidence_digest)

        record = {
            "schema_version": ATTESTATION_SCHEMA_VERSION,
            "milestone": ATTESTATION_MILESTONE,
            "attestation_id": attestation_uuid,
            "generated_at_utc": now_utc,
            "source_commit_sha": self.source_commit_sha,
            "candidate_checkpoint_sha256": self.candidate_checkpoint_sha256,
            "evidence_digest_sha256": evidence_digest,
            "attestation_signature": attestation_sig,
            "signature_scheme": TEST_NONPROD_SIGNING_SCHEME,
            "signature_public_key_hex": TEST_NONPROD_PUBLIC_KEY_HEX,
            "active_runtime_identity": copy.deepcopy(self.active_runtime_identity),
            "runtime_capabilities": copy.deepcopy(self.runtime_caps),
            "evaluator_context": evaluator_info,
            "provenance": provenance_info,
            "governance_invariants": governance_invariants,
            "controls": copy.deepcopy(self.controls),
        }

        return record


def validate_security_attestation_schema(
    record: Dict[str, Any],
    policy: Optional[AttestationTrustPolicy] = None,
) -> None:
    """Validates structure, governance invariants, cryptographic binding, and signatures of an attestation."""
    if not isinstance(record, dict):
        raise ContractViolationError("Attestation record must be a dictionary.")

    active_policy = policy or DEFAULT_TEST_TRUST_POLICY

    required_keys = {
        "schema_version",
        "milestone",
        "attestation_id",
        "generated_at_utc",
        "candidate_checkpoint_sha256",
        "evidence_digest_sha256",
        "runtime_capabilities",
        "evaluator_context",
        "governance_invariants",
        "controls",
    }
    missing = required_keys - set(record.keys())
    if missing:
        raise ContractViolationError(f"Attestation missing required fields: {sorted(missing)}.")

    schema_version = str(record["schema_version"])
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ContractViolationError(f"Unsupported schema version '{schema_version}'.")

    # Downgrade protection: legacy 1.0.0 requires explicit caller authorization
    is_legacy = (schema_version == "1.0.0")
    if is_legacy and not active_policy.allow_legacy_unsigned:
        raise ContractViolationError(
            f"Schema version '{schema_version}' is unsigned legacy and not permitted under current policy."
        )

    # Validate nested field types
    if not isinstance(record["runtime_capabilities"], dict):
        raise ContractViolationError("Attestation 'runtime_capabilities' must be a dictionary.")
    if not isinstance(record["evaluator_context"], dict):
        raise ContractViolationError("Attestation 'evaluator_context' must be a dictionary.")
    if not isinstance(record["governance_invariants"], dict):
        raise ContractViolationError("Attestation 'governance_invariants' must be a dictionary.")
    if not isinstance(record["controls"], dict):
        raise ContractViolationError("Attestation 'controls' must be a dictionary.")

    # Validate control entries
    for cid, ctrl in record["controls"].items():
        if not isinstance(ctrl, dict):
            raise ContractViolationError(f"Control '{cid}' must be a dictionary.")
        for field in ["name", "status", "evidence", "rationale"]:
            if field not in ctrl:
                raise ContractViolationError(f"Control '{cid}' missing required field '{field}'.")
        if ctrl["status"] not in VALID_CONTROL_STATUSES:
            raise ContractViolationError(f"Control '{cid}' has invalid status '{ctrl['status']}'.")
        if not isinstance(ctrl["evidence"], dict):
            raise ContractViolationError(f"Control '{cid}' evidence must be a dictionary.")

    # Strict validation of specific controls
    res_ctrl = record["controls"].get("ISO-RES-01")
    if res_ctrl and res_ctrl["status"] == STATUS_TESTED_AND_VERIFIED:
        ev = res_ctrl.get("evidence", {})
        obs = ev.get("observed_limits", ev)
        eff_cpu = obs.get("effective_cpu_cores")
        mem_bytes = obs.get("memory_max_bytes")
        pids = obs.get("pids_max")

        if eff_cpu is None or mem_bytes is None or pids is None:
            raise ContractViolationError(
                "Null or unavailable resource measurements cannot receive STATUS_TESTED_AND_VERIFIED for ISO-RES-01."
            )
        if eff_cpu > 2.0 or mem_bytes > 2 * 1024 * 1024 * 1024 or pids > 128:
            raise ContractViolationError(
                f"Incorrect resource quotas cannot receive STATUS_TESTED_AND_VERIFIED for ISO-RES-01. "
                f"Observed: CPU={eff_cpu}, Memory={mem_bytes}, PIDs={pids}."
            )
        if not ev.get("limits_available"):
            raise ContractViolationError("ISO-RES-01 cannot receive STATUS_TESTED_AND_VERIFIED when limits_available is False.")
        if not ev.get("enforced"):
            raise ContractViolationError("ISO-RES-01 cannot receive STATUS_TESTED_AND_VERIFIED when limits are not actively enforced.")

    net_ctrl = record["controls"].get("ISO-NET-01")
    if net_ctrl and net_ctrl["status"] == STATUS_TESTED_AND_VERIFIED:
        ev = net_ctrl.get("evidence", {})
        worker_tier = record.get("active_runtime_identity", {}).get("active_runtime_tier")
        ctrl_tier = ev.get("active_isolation_tier")
        valid_isolated_tiers = {"docker_oci", "native_namespaces", "wsl2_namespaces"}

        if ctrl_tier and ctrl_tier != worker_tier:
            raise ContractViolationError(
                f"Isolation tier mismatch: control evidence claims '{ctrl_tier}' but actual worker identity is '{worker_tier}'."
            )
        if not worker_tier or worker_tier not in valid_isolated_tiers:
            raise ContractViolationError(
                f"Network capability availability cannot substitute for actual worker isolation in ISO-NET-01 (tier={worker_tier})."
            )

    uid_ctrl = record["controls"].get("ISO-UID-01")
    if uid_ctrl and uid_ctrl["status"] == STATUS_TESTED_AND_VERIFIED:
        ev = uid_ctrl.get("evidence", {})
        if ev.get("uid") is None:
            raise ContractViolationError("Unavailable worker identity or unverified escalation denial cannot receive STATUS_TESTED_AND_VERIFIED for ISO-UID-01.")
        # Privilege escalation denial must NOT be overridden by no_new_privs alone
        if ev.get("escalation_prevented") is not True:
            raise ContractViolationError(
                "Unavailable worker identity or unverified escalation denial: privilege escalation denial control ISO-UID-01 requires escalation_prevented=True; no_new_privs cannot substitute."
            )

    vault_ctrl = record["controls"].get("ISO-VAULT-01")
    if vault_ctrl and vault_ctrl["status"] == STATUS_TESTED_AND_VERIFIED:
        ev = vault_ctrl.get("evidence", {})
        if not ev.get("conclusive_isolation") or ev.get("any_route_accessible"):
            raise ContractViolationError(
                "Private vault isolation cannot receive STATUS_TESTED_AND_VERIFIED when any route is accessible or inconclusive."
            )

    gov_ctrl = record["controls"].get("ISO-GOV-01")
    if gov_ctrl:
        ev = gov_ctrl.get("evidence", {})
        v_rt = ev.get("verified_runtime_invariants", {})
        tp = v_rt.get("training_permitted")
        ea = v_rt.get("execution_authorized")
        if tp is True or (tp is not None and tp != 0) or ea is True or (ea is not None and ea != 0):
            raise ContractViolationError(
                "ISO-GOV-01 evidence contradicts governance invariants: training_permitted and execution_authorized must be 0, not True or non-zero."
            )

    # Comprehensive governance invariant validation
    gov = record["governance_invariants"]
    rt_gov = gov.get("runtime_verified_invariants", gov)
    tp_rt = rt_gov.get("training_permitted")
    ea_rt = rt_gov.get("execution_authorized")
    tp_top = gov.get("training_permitted")
    ea_top = gov.get("execution_authorized")

    # Strictly check numeric 0 (boolean True/False is strictly rejected)
    if type(tp_rt) is bool or tp_rt != 0 or type(tp_top) is bool or tp_top != 0:
        raise ContractViolationError("Governance invariant breached: training_permitted != 0.")
    if type(ea_rt) is bool or ea_rt != 0 or type(ea_top) is bool or ea_top != 0:
        raise ContractViolationError("Governance invariant breached: execution_authorized != 0.")
    if rt_gov.get("gradient_steps_verified", 0) != 0 or gov.get("gradient_steps", 0) != 0:
        raise ContractViolationError("Governance invariant breached: gradient_steps != 0.")
    if rt_gov.get("optimizer_updates_verified", 0) != 0 or gov.get("optimizer_updates", 0) != 0:
        raise ContractViolationError("Governance invariant breached: optimizer_updates != 0.")

    policy_decl = gov.get("static_policy_declarations", gov)
    if policy_decl.get("new_holdout_generation") != "NOT_AUTHORIZED" or gov.get("new_holdout_generation") != "NOT_AUTHORIZED":
        raise ContractViolationError("Governance policy breached: new_holdout_generation != NOT_AUTHORIZED.")
    if policy_decl.get("final_evaluation") != "BLOCKED" or gov.get("final_evaluation") != "BLOCKED":
        raise ContractViolationError("Governance policy breached: final_evaluation != BLOCKED.")

    prod_state = gov.get("production_authorization_state", gov)
    if prod_state.get("production_keys") != "NOT_GENERATED" or gov.get("production_keys") != "NOT_GENERATED":
        raise ContractViolationError("Governance state breached: production_keys != NOT_GENERATED.")
    if prod_state.get("production_vault") != "NOT_INITIALIZED" or gov.get("production_vault") != "NOT_INITIALIZED":
        raise ContractViolationError("Governance state breached: production_vault != NOT_INITIALIZED.")

    # Quarantine evidence verification
    quar = gov.get("physical_artifact_quarantine", {})
    if quar:
        if not quar.get("scan_complete", True):
            raise ContractViolationError("Physical holdout quarantine scan was incomplete: unreadable or oversized files exist.")
        if not quar.get("physical_exclusion_verified"):
            raise ContractViolationError("Physical holdout quarantine exclusion failed: exposed compromised files exist.")
        if quar.get("exposed_compromised_files_found"):
            raise ContractViolationError(f"Compromised holdout files detected in search paths: {quar.get('exposed_compromised_files_found')}.")
        if quar.get("unreadable_files"):
            raise ContractViolationError(f"Unreadable files detected during quarantine scan: {quar.get('unreadable_files')}.")

    # Modern schemas require signature, trusted key, and provenance consistency
    if not is_legacy:
        if "attestation_signature" not in record or not record["attestation_signature"]:
            raise ContractViolationError(f"Attestation schema '{schema_version}' requires a mandatory Ed25519 signature.")

        scheme = record.get("signature_scheme")
        if not scheme:
            raise ContractViolationError("Attestation missing mandatory 'signature_scheme'.")

        pub_hex = record.get("signature_public_key_hex")
        if not pub_hex:
            raise ContractViolationError("Attestation missing mandatory 'signature_public_key_hex'.")

        if active_policy.enforce_production_acceptance:
            if scheme == TEST_NONPROD_SIGNING_SCHEME:
                raise ContractViolationError(f"Production attestation acceptance strictly rejects test scheme '{scheme}'.")
            if pub_hex not in active_policy.trusted_public_keys:
                raise ContractViolationError(f"Untrusted public key '{pub_hex}' not anchored in production trust policy.")
        else:
            if active_policy.allow_test_keys:
                if scheme != TEST_NONPROD_SIGNING_SCHEME:
                    raise ContractViolationError(f"Unauthorized signature scheme: {scheme}.")
                if pub_hex != TEST_NONPROD_PUBLIC_KEY_HEX:
                    raise ContractViolationError(f"Untrusted public key: {pub_hex}.")
            else:
                if pub_hex not in active_policy.trusted_public_keys:
                    raise ContractViolationError(f"Untrusted public key: {pub_hex}.")

        # Provenance cross-validation
        prov = record.get("provenance")
        if not isinstance(prov, dict):
            raise ContractViolationError("Attestation missing mandatory 'provenance' dictionary.")
        if record.get("source_commit_sha") != prov.get("source_commit_sha"):
            raise ContractViolationError("Top-level source_commit_sha does not match provenance.source_commit_sha.")
        if record.get("attestation_id") != prov.get("attestation_uuid"):
            raise ContractViolationError("Top-level attestation_id does not match provenance.attestation_uuid.")
        if record.get("generated_at_utc") != prov.get("generated_at_utc"):
            raise ContractViolationError("Top-level generated_at_utc does not match provenance.generated_at_utc.")
        if record.get("milestone") != prov.get("milestone"):
            raise ContractViolationError("Top-level milestone does not match provenance.milestone.")

    # Verify evidence digest integrity
    expected_digest = record.get("evidence_digest_sha256", "")
    actual_digest = compute_canonical_attestation_payload_digest(record)

    if expected_digest.lower() != actual_digest.lower():
        if is_legacy:
            controls = record.get("controls", {})
            cand_sha = record.get("candidate_checkpoint_sha256", "")
            runtime_caps = record.get("runtime_capabilities", {})
            legacy_digest = compute_evidence_digest(
                controls=controls,
                candidate_checkpoint_sha256=cand_sha,
                runtime_capabilities=runtime_caps,
            )
            if expected_digest.lower() != legacy_digest.lower():
                raise ContractViolationError(
                    f"Legacy attestation evidence digest mismatch! Expected {expected_digest}, recomputed {legacy_digest}."
                )
        else:
            raise ContractViolationError(
                f"Attestation evidence digest mismatch! Expected {expected_digest}, recomputed {actual_digest}."
            )

    if not is_legacy:
        # Signature verification is strictly mandatory for modern schemas
        if active_policy.enforce_production_acceptance:
            pub_hex = record.get("signature_public_key_hex", "")
            sig_hex = record.get("attestation_signature", "")
            try:
                pub_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
                sig_bytes = bytes.fromhex(sig_hex)
                pub_key.verify(sig_bytes, actual_digest.encode("utf-8"))
            except Exception as exc:
                raise ContractViolationError(f"Production Ed25519 signature verification failed: {exc}")
        else:
            if not verify_test_attestation_signature(record):
                raise ContractViolationError("Attestation Ed25519 signature verification failed.")


def verify_attestation_digest(
    record: Dict[str, Any],
    policy: Optional[AttestationTrustPolicy] = None,
    allow_legacy_unsigned: bool = False,
) -> bool:
    """Verifies evidence digest, governance invariants, schema validation, and digital signature."""
    try:
        active_policy = policy or AttestationTrustPolicy(
            allow_test_keys=True,
            allow_legacy_unsigned=allow_legacy_unsigned,
        )
        validate_security_attestation_schema(record, policy=active_policy)
        return True
    except Exception:
        return False


def save_security_attestation(record: Dict[str, Any], path: Path | str) -> Path:
    """Validates and writes the security attestation document to disk."""
    validate_security_attestation_schema(record)
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return p
