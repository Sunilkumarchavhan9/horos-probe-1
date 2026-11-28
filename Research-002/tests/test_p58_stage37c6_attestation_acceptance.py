"""HOROS PROBE-1 — Stage 3.7C.6
Attestation Acceptance Hardening & Independent Runtime Verification Test Suite (P58).

Coverage:
- P58-01: Public test-key signature rejected by production acceptance.
- P58-02: Explicit test-only signing verification.
- P58-03: Modern schema downgrade rejected.
- P58-04: Schema and provenance mutation invalidates signature.
- P58-05: Missing or corrupted signature rejected.
- P58-06: Untrusted key substitution rejected.
- P58-07: Correct source commit and source-tree provenance.
- P58-08: Unknown or dirty source provenance rejected for trusted acceptance.
- P58-09: NPZ registry digest detection.
- P58-10: Privileged JSON registry digest detection.
- P58-11: Quarantine-named copy directory remains scanned.
- P58-12: Incomplete scan cannot produce clean verification.
- P58-13: Registry path and registry hash integrity.
- P58-14: Isolation tier mismatch rejected.
- P58-15: `no_new_privs` cannot override failed escalation denial.
- P58-16: Missing or unenforced resource measurements rejected.
- P58-17: Governance contradiction rejected.
- P58-18: Docker-only engine selection.
- P58-19: Podman-only engine selection.
- P58-20: Missing OCI runtime fails closed.
- P58-21: Checkpoint path and mount verification.
- P58-22: Authentic test record passes only its authorized test verification path.
- P58-23: Actual runtime evidence cannot be replaced by claimed environment metadata.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock, patch

import pytest
import torch
from cryptography.hazmat.primitives.asymmetric import ed25519

from world_model_vnext.contracts import (
    CANONICAL_QUARANTINE_REGISTRY_PATH,
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_PROTOCOL,
    COMPROMISED_HOLDOUT_STATUS,
    EXPECTED_QUARANTINE_REGISTRY_SHA256,
    ContractViolationError,
    LOCKED_HCTT_CONFIG,
    get_compromised_npz_sha256,
    get_compromised_privileged_sha256,
    get_compromised_sample_ids,
    load_quarantine_record,
    reset_quarantine_caches,
    verify_quarantine_registry_integrity,
)
from world_model_vnext.hctt import HCTTWorldModel
from world_model_vnext.runner import EXECUTION_AUTHORIZED, TRAINING_PERMITTED
from world_model_vnext.runtime_isolation import (
    EXPECTED_CPU_CORES,
    EXPECTED_MEMORY_BYTES,
    EXPECTED_PIDS_MAX,
    PROFILE_AUTO,
    PROFILE_DOCKER_OCI,
    PROFILE_NATIVE_NAMESPACES,
    PROFILE_SUBPROCESS_FALLBACK,
    PROFILE_WSL2_NAMESPACES,
    build_namespace_run_command,
    build_oci_run_command,
    detect_runtime_capabilities,
    resolve_execution_profile,
    translate_to_wsl_path,
)
from world_model_vnext.security_attestation import (
    ATTESTATION_MILESTONE,
    ATTESTATION_SCHEMA_VERSION,
    DEFAULT_LEGACY_AUDIT_POLICY,
    DEFAULT_PRODUCTION_TRUST_POLICY,
    DEFAULT_TEST_TRUST_POLICY,
    STATUS_BLOCKED,
    STATUS_IMPLEMENTED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_TESTED_AND_VERIFIED,
    TEST_NONPROD_PUBLIC_KEY_HEX,
    TEST_NONPROD_SIGNING_SCHEME,
    AttestationTrustPolicy,
    SecurityAttestationBuilder,
    compute_canonical_attestation_payload_digest,
    compute_evidence_digest,
    compute_source_tree_digest,
    get_source_commit_sha,
    is_source_tree_dirty,
    save_security_attestation,
    sign_attestation_digest_test_only,
    validate_security_attestation_schema,
    verify_attestation_digest,
    verify_attestation_signature,
    verify_physical_holdout_quarantine,
    verify_production_attestation_acceptance,
    verify_test_attestation_acceptance,
    verify_test_attestation_signature,
)
from world_model_vnext.worker_isolation import (
    IsolatedInferenceWorker,
    apply_os_vault_isolation,
    remove_os_vault_isolation,
)


@pytest.fixture
def synthetic_checkpoint(tmp_path: Path) -> Tuple[Path, str]:
    """Creates a valid synthetic candidate checkpoint and computes its SHA-256 digest."""
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    ckpt_path = tmp_path / "candidate_model_p58.pt"
    torch.save(model.state_dict(), ckpt_path)
    sha256 = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
    return ckpt_path, sha256


# ==============================================================================
# P58.1 & P58.2: Cryptographic Trust, Key Separation, & Downgrade Resistance
# ==============================================================================

def test_p58_01_public_test_key_signature_rejected_by_production_acceptance(synthetic_checkpoint: Tuple[Path, str]):
    """P58-01: An attestation signed with the publicly known test key is strictly rejected by production acceptance."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    # Must fail under production acceptance
    with pytest.raises(ContractViolationError, match="Production attestation acceptance strictly rejects test"):
        verify_production_attestation_acceptance(record)

    with pytest.raises(ContractViolationError, match="Production attestation acceptance strictly rejects test"):
        validate_security_attestation_schema(record, policy=DEFAULT_PRODUCTION_TRUST_POLICY)


def test_p58_02_explicit_test_only_signing_verification(synthetic_checkpoint: Tuple[Path, str]):
    """P58-02: A test-key signature can be verified only through the authorized test-only pathway."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    # Authorized test pathways succeed
    assert verify_test_attestation_signature(record) is True
    assert verify_test_attestation_acceptance(record) is True
    assert verify_attestation_digest(record, policy=DEFAULT_TEST_TRUST_POLICY) is True


def test_p58_03_modern_schema_downgrade_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """P58-03: Modern attestation cannot downgrade to schema 1.0.0 without explicit legacy caller policy."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    # Attack: downgrade to 1.0.0, strip signature, recompute legacy digest
    record["schema_version"] = "1.0.0"
    record.pop("attestation_signature", None)
    legacy_digest = compute_evidence_digest(
        controls=record["controls"],
        candidate_checkpoint_sha256=record["candidate_checkpoint_sha256"],
        runtime_capabilities=record["runtime_capabilities"],
    )
    record["evidence_digest_sha256"] = legacy_digest

    # Standard / default validation must reject unsigned legacy downgrade
    with pytest.raises(ContractViolationError, match="unsigned legacy and not permitted"):
        validate_security_attestation_schema(record, policy=DEFAULT_TEST_TRUST_POLICY)

    assert verify_attestation_digest(record) is False

    # Only an explicitly authorized legacy audit policy can inspect it
    validate_security_attestation_schema(record, policy=DEFAULT_LEGACY_AUDIT_POLICY)
    assert verify_attestation_digest(record, policy=DEFAULT_LEGACY_AUDIT_POLICY) is True


def test_p58_04_schema_and_provenance_mutation_invalidates_signature(synthetic_checkpoint: Tuple[Path, str]):
    """P58-04: Modifying any envelope or provenance field breaks cryptographic binding."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    base_record = builder.build()

    # 1. Mutate schema_version
    r1 = copy.deepcopy(base_record)
    r1["schema_version"] = "2.1.0"
    with pytest.raises(ContractViolationError):
        verify_test_attestation_acceptance(r1)

    # 2. Mutate attestation_id
    r2 = copy.deepcopy(base_record)
    r2["attestation_id"] = str(uuid.uuid4())
    with pytest.raises(ContractViolationError):
        verify_test_attestation_acceptance(r2)

    # 3. Mutate generated_at_utc
    r3 = copy.deepcopy(base_record)
    r3["generated_at_utc"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ContractViolationError):
        verify_test_attestation_acceptance(r3)

    # 4. Mutate milestone
    r4 = copy.deepcopy(base_record)
    r4["milestone"] = "Stage 3.7X Fabricated Milestone"
    with pytest.raises(ContractViolationError):
        verify_test_attestation_acceptance(r4)

    # 5. Mutate provenance commit
    r5 = copy.deepcopy(base_record)
    r5["provenance"]["source_commit_sha"] = "0000000000000000000000000000000000000000"
    with pytest.raises(ContractViolationError):
        verify_test_attestation_acceptance(r5)


def test_p58_05_missing_or_corrupted_signature_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """P58-05: Missing or corrupted Ed25519 signature is strictly rejected."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    base_record = builder.build()

    # Missing signature
    r_missing = copy.deepcopy(base_record)
    r_missing.pop("attestation_signature")
    with pytest.raises(ContractViolationError, match="requires a mandatory Ed25519 signature"):
        validate_security_attestation_schema(r_missing)

    # Corrupted signature bytes
    r_corrupt = copy.deepcopy(base_record)
    r_corrupt["attestation_signature"] = "deadbeef" * 16
    with pytest.raises(ContractViolationError, match="signature verification failed"):
        validate_security_attestation_schema(r_corrupt)


def test_p58_06_untrusted_key_substitution_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """P58-06: An attacker cannot make a record trusted by signing it with their own key and substituting public key."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    # Attacker generates arbitrary Ed25519 key pair
    attacker_priv = ed25519.Ed25519PrivateKey.generate()
    attacker_pub = attacker_priv.public_key().public_bytes_raw().hex()

    digest = record["evidence_digest_sha256"]
    attacker_sig = attacker_priv.sign(digest.encode("utf-8")).hex()

    record["signature_public_key_hex"] = attacker_pub
    record["attestation_signature"] = attacker_sig

    # Rejected in test pathway because it doesn't match the dedicated test public key
    with pytest.raises(ContractViolationError, match="Untrusted public key"):
        verify_test_attestation_acceptance(record)

    # Rejected in production pathway because it's not anchored in production trust policy
    with pytest.raises(ContractViolationError):
        verify_production_attestation_acceptance(record)


# ==============================================================================
# P58.3: Source Provenance and Reproducibility
# ==============================================================================

def test_p58_07_correct_source_commit_and_source_tree_provenance(synthetic_checkpoint: Tuple[Path, str]):
    """P58-07: Verifies source commit SHA resolution and deterministic source tree hashing."""
    sha = get_source_commit_sha()
    assert isinstance(sha, str)
    assert len(sha) == 40 or sha == "UNKNOWN_UNCOMMITTED_REVISION"

    tree_digest = compute_source_tree_digest()
    assert isinstance(tree_digest, str)
    assert len(tree_digest) == 64

    # Build attestation and verify provenance fields
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()
    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    assert "provenance" in record
    prov = record["provenance"]
    assert prov["source_tree_digest_sha256"] == tree_digest
    assert "is_dirty_source_tree" in prov


def test_p58_08_unknown_or_dirty_source_provenance_rejected_for_trusted_acceptance(synthetic_checkpoint: Tuple[Path, str]):
    """P58-08: Dirty source trees or unknown revisions cannot be accepted as trusted production provenance."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    base_record = builder.build()

    # Generate a simulated production key pair
    fake_prod_priv = ed25519.Ed25519PrivateKey.generate()
    fake_prod_pub_hex = fake_prod_priv.public_key().public_bytes_raw().hex()
    prod_policy = AttestationTrustPolicy(
        trusted_public_keys=frozenset({fake_prod_pub_hex}),
        enforce_production_acceptance=True,
    )

    # 1. Dirty source tree rejected in production
    r_dirty = copy.deepcopy(base_record)
    r_dirty["signature_scheme"] = "Ed25519-PROD"
    r_dirty["signature_public_key_hex"] = fake_prod_pub_hex
    r_dirty["provenance"]["is_dirty_source_tree"] = True
    digest_dirty = compute_canonical_attestation_payload_digest(r_dirty)
    r_dirty["evidence_digest_sha256"] = digest_dirty
    r_dirty["attestation_signature"] = fake_prod_priv.sign(digest_dirty.encode("utf-8")).hex()
    with pytest.raises(ContractViolationError, match="strictly rejects dirty source trees"):
        verify_production_attestation_acceptance(r_dirty, trust_policy=prod_policy)

    # 2. Unknown uncommitted revision rejected in production
    r_unknown = copy.deepcopy(base_record)
    r_unknown["signature_scheme"] = "Ed25519-PROD"
    r_unknown["signature_public_key_hex"] = fake_prod_pub_hex
    r_unknown["provenance"]["is_dirty_source_tree"] = False
    r_unknown["source_commit_sha"] = "UNKNOWN_UNCOMMITTED_REVISION"
    r_unknown["provenance"]["source_commit_sha"] = "UNKNOWN_UNCOMMITTED_REVISION"
    digest_unknown = compute_canonical_attestation_payload_digest(r_unknown)
    r_unknown["evidence_digest_sha256"] = digest_unknown
    r_unknown["attestation_signature"] = fake_prod_priv.sign(digest_unknown.encode("utf-8")).hex()
    with pytest.raises(ContractViolationError, match="strictly requires verified Git commit SHA"):
        verify_production_attestation_acceptance(r_unknown, trust_policy=prod_policy)


# ==============================================================================
# P58.4: Authoritative Quarantine Verification & Incomplete Scan Detection
# ==============================================================================

def test_p58_09_npz_registry_digest_detection(tmp_path: Path):
    """P58-09: Verifies that NPZ content matching an authoritative registry digest is detected."""
    npz_digests = get_compromised_npz_sha256()
    assert len(npz_digests) > 0
    target_digest = next(iter(npz_digests))

    scan_dir = tmp_path / "data" / "leak_npz"
    scan_dir.mkdir(parents=True)
    leaked_file = scan_dir / "unrelated_name_001.npz"
    leaked_file.write_bytes(b"content_to_be_matched")

    orig_sha256 = hashlib.sha256
    def mock_sha(data=b""):
        h = orig_sha256(data)
        if data == b"content_to_be_matched":
            class MockHash:
                def hexdigest(self):
                    return target_digest
            return MockHash()
        return h

    with patch("hashlib.sha256", side_effect=mock_sha):
        scan_res = verify_physical_holdout_quarantine([scan_dir])
        assert scan_res["physical_exclusion_verified"] is False
        assert any("unrelated_name_001.npz" in f for f in scan_res["exposed_compromised_files_found"])


def test_p58_10_privileged_json_registry_digest_detection(tmp_path: Path):
    """P58-10: Verifies that privileged JSON matching an authoritative registry digest is detected."""
    priv_digests = get_compromised_privileged_sha256()
    assert len(priv_digests) > 0
    target_digest = next(iter(priv_digests))

    scan_dir = tmp_path / "data" / "leak_priv"
    scan_dir.mkdir(parents=True)
    leaked_file = scan_dir / "audit_dump.json"
    leaked_file.write_bytes(b"dummy_privileged_json_bytes")

    orig_sha256 = hashlib.sha256
    def mock_sha(data=b""):
        h = orig_sha256(data)
        if data == b"dummy_privileged_json_bytes":
            class MockHash:
                def hexdigest(self):
                    return target_digest
            return MockHash()
        return h

    with patch("hashlib.sha256", side_effect=mock_sha):
        scan_res = verify_physical_holdout_quarantine([scan_dir])
        assert scan_res["physical_exclusion_verified"] is False
        assert any("audit_dump.json" in f for f in scan_res["exposed_compromised_files_found"])


def test_p58_11_quarantine_named_copy_directory_remains_scanned(tmp_path: Path):
    """P58-11: A copy of a quarantine directory must not be exempted from recursive scanning."""
    copy_dir = tmp_path / "data" / "stage36_holdout_quarantine_copy"
    copy_dir.mkdir(parents=True)
    leaked_file = copy_dir / "stage35_final_holdout_leaked.npz"
    leaked_file.write_bytes(b"leaked_sample_data")

    scan_res = verify_physical_holdout_quarantine([copy_dir])
    assert scan_res["physical_exclusion_verified"] is False
    assert any("stage35_final_holdout_leaked.npz" in f for f in scan_res["exposed_compromised_files_found"])


def test_p58_12_incomplete_scan_cannot_produce_clean_verification(tmp_path: Path, synthetic_checkpoint: Tuple[Path, str]):
    """P58-12: Unreadable or oversized candidates cause scan_complete=False and fail clean verification."""
    scan_dir = tmp_path / "data" / "oversized_test"
    scan_dir.mkdir(parents=True)
    huge_file = scan_dir / "huge_archive.npz"
    with open(huge_file, "wb") as f:
        f.seek(101 * 1024 * 1024)
        f.write(b"\0")

    scan_res = verify_physical_holdout_quarantine([scan_dir])
    assert scan_res["scan_complete"] is False
    assert scan_res["physical_exclusion_verified"] is False
    assert len(scan_res["unreadable_files"]) > 0

    # Attestation builder with incomplete quarantine evidence must fail validation
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()
    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()
    record["governance_invariants"]["physical_artifact_quarantine"] = scan_res
    with pytest.raises(ContractViolationError, match="incomplete"):
        validate_security_attestation_schema(record)


def test_p58_13_registry_path_and_registry_hash_integrity():
    """P58-13: Verifies that registry path tampering or invalid registry hash fails closed."""
    # 1. Valid registry passes
    reg_sha = verify_quarantine_registry_integrity()
    assert reg_sha == EXPECTED_QUARANTINE_REGISTRY_SHA256

    # 2. Tampered hash fails closed
    try:
        reset_quarantine_caches()
        with patch("world_model_vnext.contracts.EXPECTED_QUARANTINE_REGISTRY_SHA256", "0" * 64):
            with pytest.raises(ContractViolationError, match="Quarantine registry SHA-256 mismatch"):
                verify_quarantine_registry_integrity()
    finally:
        reset_quarantine_caches()

    # 3. Missing path fails closed
    try:
        reset_quarantine_caches()
        with patch("world_model_vnext.contracts.CANONICAL_QUARANTINE_REGISTRY_PATH", Path("non_existent_registry.json")):
            with pytest.raises(ContractViolationError, match="Mandatory quarantine record missing"):
                verify_quarantine_registry_integrity()
    finally:
        reset_quarantine_caches()


# ==============================================================================
# P58.5: Runtime Identity & Security Evidence Consistency
# ==============================================================================

def test_p58_14_isolation_tier_mismatch_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """P58-14: Reject attestation when control isolation tier disagrees with actual worker runtime tier."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-NET-01",
        name="Network Isolation",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"active_isolation_tier": "docker_oci"},  # Contradicts subprocess_fallback!
    )
    with pytest.raises(ContractViolationError, match="Isolation tier mismatch"):
        validate_security_attestation_schema(builder.build())


def test_p58_15_no_new_privs_cannot_override_failed_escalation_denial(synthetic_checkpoint: Tuple[Path, str]):
    """P58-15: no_new_privs=True cannot substitute for failed escalation denial in ISO-UID-01."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-UID-01",
        name="Worker Identity",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "uid": 10001,
            "no_new_privs": True,
            "escalation_prevented": False,  # Failed escalation denial!
        },
    )
    with pytest.raises(ContractViolationError, match="Unavailable worker identity or unverified escalation denial"):
        validate_security_attestation_schema(builder.build())


def test_p58_16_missing_or_unenforced_resource_measurements_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """P58-16: Null, unenforced, or over-quota resource measurements reject TESTED_AND_VERIFIED."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )

    # 1. Unenforced limits
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "limits_available": True,
            "enforced": False,
            "observed_limits": {"effective_cpu_cores": 2.0, "memory_max_bytes": 2147483648, "pids_max": 128},
        },
    )
    with pytest.raises(ContractViolationError, match="limits are not actively enforced"):
        validate_security_attestation_schema(builder.build())

    # 2. Over-quota CPU
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "limits_available": True,
            "enforced": True,
            "observed_limits": {"effective_cpu_cores": 4.0, "memory_max_bytes": 2147483648, "pids_max": 128},
        },
    )
    with pytest.raises(ContractViolationError, match="Incorrect resource quotas"):
        validate_security_attestation_schema(builder.build())


def test_p58_17_governance_contradiction_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """P58-17: Contradictory governance evidence or boolean representations reject verification."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )

    # 1. Top-level governance breach: training_permitted != 0
    record = builder.build()
    record["governance_invariants"]["training_permitted"] = 1
    with pytest.raises(ContractViolationError, match="training_permitted != 0"):
        validate_security_attestation_schema(record)

    # 2. Top-level governance breach: final_evaluation != BLOCKED
    record2 = builder.build()
    record2["governance_invariants"]["final_evaluation"] = "PERMITTED"
    with pytest.raises(ContractViolationError, match="final_evaluation != BLOCKED"):
        validate_security_attestation_schema(record2)


# ==============================================================================
# P58.6: Docker and Podman Backend Engine Selection
# ==============================================================================

def test_p58_18_docker_only_engine_selection(tmp_path: Path):
    """P58-18: When Docker is available and Podman is not, Docker engine is selected."""
    with patch("world_model_vnext.worker_isolation.detect_runtime_capabilities") as mock_caps:
        mock_caps.return_value = {
            "docker_available": True,
            "podman_available": False,
            "wsl_unshare_available": False,
            "native_unshare_available": False,
        }
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None
            mock_proc.returncode = None
            mock_popen.return_value = mock_proc
            worker = IsolatedInferenceWorker(working_dir=tmp_path, execution_profile=PROFILE_DOCKER_OCI)
            try:
                assert worker.selected_oci_engine == "docker"
                assert worker.get_runtime_identity()["selected_oci_engine"] == "docker"
                assert worker.oci_run_cmd[0] == "docker"
            finally:
                worker.close()


def test_p58_19_podman_only_engine_selection(tmp_path: Path):
    """P58-19: When Podman is available and Docker is not, Podman engine is selected."""
    with patch("world_model_vnext.worker_isolation.detect_runtime_capabilities") as mock_caps:
        mock_caps.return_value = {
            "docker_available": False,
            "podman_available": True,
            "wsl_unshare_available": False,
            "native_unshare_available": False,
        }
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None
            mock_proc.returncode = None
            mock_popen.return_value = mock_proc
            worker = IsolatedInferenceWorker(working_dir=tmp_path, execution_profile=PROFILE_DOCKER_OCI)
            try:
                assert worker.selected_oci_engine == "podman"
                assert worker.get_runtime_identity()["selected_oci_engine"] == "podman"
                assert worker.oci_run_cmd[0] == "podman"
            finally:
                worker.close()


def test_p58_20_missing_oci_runtime_fails_closed(tmp_path: Path):
    """P58-20: Missing OCI runtime engine fails closed without silent fallback."""
    with patch("world_model_vnext.worker_isolation.detect_runtime_capabilities") as mock_caps:
        mock_caps.return_value = {
            "docker_available": False,
            "podman_available": False,
            "wsl_unshare_available": False,
            "native_unshare_available": False,
        }
        with pytest.raises(ContractViolationError, match="Docker/Podman engine not available"):
            IsolatedInferenceWorker(working_dir=tmp_path, execution_profile=PROFILE_DOCKER_OCI)


def test_p58_21_checkpoint_path_and_mount_verification(tmp_path: Path):
    """P58-21: Checkpoint host-to-container path mapping mounts read-only to /model/checkpoint.pt."""
    ckpt = tmp_path / "model.pt"
    ckpt.write_bytes(b"dummy_weights")

    # Command with checkpoint
    cmd = build_oci_run_command(checkpoint_path=ckpt, container_engine="docker")
    cmd_str = " ".join(cmd)
    assert "-v" in cmd
    assert ":/model/checkpoint.pt:ro" in cmd_str
    assert cmd[0] == "docker"

    # Command with Podman engine
    cmd_podman = build_oci_run_command(checkpoint_path=ckpt, container_engine="podman")
    assert cmd_podman[0] == "podman"
    assert ":/model/checkpoint.pt:ro" in " ".join(cmd_podman)

    # Checkpoint-free security probe
    cmd_probe = build_oci_run_command(checkpoint_path=None)
    assert ":/model/checkpoint.pt" not in " ".join(cmd_probe)


# ==============================================================================
# P58.7: Acceptance Pathways & Actual Runtime Evidence
# ==============================================================================

def test_p58_22_authentic_test_record_passes_only_its_authorized_test_verification_path(synthetic_checkpoint: Tuple[Path, str]):
    """P58-22: Authentic builder-generated records pass test acceptance but strictly fail production acceptance."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    # Pass in authorized test verification
    assert verify_test_attestation_acceptance(record) is True
    assert verify_attestation_digest(record, policy=DEFAULT_TEST_TRUST_POLICY) is True

    # Strictly rejected by production acceptance
    with pytest.raises(ContractViolationError, match="Production attestation acceptance strictly rejects test"):
        verify_production_attestation_acceptance(record)


def test_p58_23_actual_runtime_evidence_cannot_be_replaced_by_claimed_environment_metadata(synthetic_checkpoint: Tuple[Path, str]):
    """P58-23: Claiming isolation in environment variables cannot substitute for actual runtime evidence."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": PROFILE_SUBPROCESS_FALLBACK},
    )
    builder.record_control(
        control_id="ISO-NET-01",
        name="Network Isolation",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"active_isolation_tier": PROFILE_SUBPROCESS_FALLBACK, "host_isolation_supported": True},
    )
    # Even if environment claims isolation, subprocess_fallback cannot receive verified network isolation
    with patch.dict(os.environ, {"HOROS_WORKER_ISOLATION_TIER": "docker_oci"}):
        with pytest.raises(ContractViolationError, match="Network capability availability cannot substitute"):
            validate_security_attestation_schema(builder.build())
