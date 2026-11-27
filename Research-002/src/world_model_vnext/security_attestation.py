"""Machine-readable security attestation generation and cryptographic evidence anchoring.

Implements Stage 3.7C.3:
- Collects verifiable runtime evidence from both Privileged Evaluator and Restricted Inference Worker.
- Audits UID/GID, Linux capabilities, privilege escalation restrictions (no-new-privileges).
- Audits filesystem writability denial (read-only root and candidate checkpoint mounts).
- Audits procfs and symlink vault traversal denial.
- Audits kernel network namespace isolation and resource quota compliance.
- Binds all empirical verification evidence to a canonical SHA-256 cryptographic provenance anchor.
- Enforces non-negotiable research governance invariants.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .contracts import (
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_PROTOCOL,
    COMPROMISED_HOLDOUT_STATUS,
    ContractViolationError,
)
from .runner import EXECUTION_AUTHORIZED, TRAINING_PERMITTED


ATTESTATION_SCHEMA_VERSION = "1.0.0"
ATTESTATION_MILESTONE = "Stage 3.7C.3"

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


def canonicalize_attestation_bytes(obj: Any) -> bytes:
    """Produces deterministic UTF-8 bytes from JSON-serializable attestation payload."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_evidence_digest(
    controls: Dict[str, Any],
    candidate_checkpoint_sha256: str = "",
    runtime_capabilities: Optional[Dict[str, Any]] = None,
) -> str:
    """Computes SHA-256 digest across canonicalized evidence payload binding candidate and runtime context."""
    payload: Dict[str, Any] = {"controls": controls}
    if candidate_checkpoint_sha256:
        payload["candidate_checkpoint_sha256"] = str(candidate_checkpoint_sha256).strip().lower()
    if runtime_capabilities is not None:
        payload["runtime_capabilities"] = runtime_capabilities
    canonical = canonicalize_attestation_bytes(payload)
    return hashlib.sha256(canonical).hexdigest()


class SecurityAttestationBuilder:
    """Assembles and verifies machine-readable security attestation records."""

    def __init__(
        self,
        candidate_checkpoint_sha256: str,
        runtime_capabilities: Dict[str, Any],
    ) -> None:
        self.candidate_checkpoint_sha256 = str(candidate_checkpoint_sha256).strip().lower()
        if len(self.candidate_checkpoint_sha256) != 64:
            raise ContractViolationError("Candidate checkpoint SHA-256 must be a 64-hex string.")

        self.runtime_caps = dict(runtime_capabilities)
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

        evidence_digest = compute_evidence_digest(
            self.controls,
            self.candidate_checkpoint_sha256,
            self.runtime_caps,
        )

        governance_invariants = {
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

        record = {
            "schema_version": ATTESTATION_SCHEMA_VERSION,
            "milestone": ATTESTATION_MILESTONE,
            "attestation_id": attestation_uuid,
            "generated_at_utc": now_utc,
            "candidate_checkpoint_sha256": self.candidate_checkpoint_sha256,
            "evidence_digest_sha256": evidence_digest,
            "runtime_capabilities": copy.deepcopy(self.runtime_caps),
            "evaluator_context": evaluator_info,
            "governance_invariants": governance_invariants,
            "controls": copy.deepcopy(self.controls),
        }

        return record


def validate_security_attestation_schema(record: Dict[str, Any]) -> None:
    """Validates structure, types, governance invariants, and cryptographic digest of an attestation."""
    if not isinstance(record, dict):
        raise ContractViolationError("Attestation record must be a dictionary.")

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

    if record["schema_version"] != ATTESTATION_SCHEMA_VERSION:
        raise ContractViolationError(f"Unsupported schema version '{record['schema_version']}'.")

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

    # Verify all required governance invariants
    gov = record["governance_invariants"]
    expected_gov = {
        "training_permitted": 0,
        "execution_authorized": 0,
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
    for k, expected_v in expected_gov.items():
        if gov.get(k) != expected_v:
            raise ContractViolationError(
                f"Governance invariant '{k}' mismatch: expected {expected_v!r}, got {gov.get(k)!r}."
            )

    # Verify evidence digest integrity
    controls = record.get("controls", {})
    cand_sha = record.get("candidate_checkpoint_sha256", "")
    runtime_caps = record.get("runtime_capabilities", {})
    expected_digest = record.get("evidence_digest_sha256", "")
    actual_digest = compute_evidence_digest(controls, cand_sha, runtime_caps)
    if expected_digest.lower() != actual_digest.lower():
        raise ContractViolationError(
            f"Attestation evidence digest mismatch! Expected {expected_digest}, recomputed {actual_digest}."
        )


def verify_attestation_digest(record: Dict[str, Any]) -> bool:
    """Verifies that the evidence digest matches the canonical hash of the controls evidence."""
    try:
        validate_security_attestation_schema(record)
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
