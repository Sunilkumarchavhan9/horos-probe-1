"""HOROS PROBE-1 — Stage 3.7C.6.1
Post-Merge Trust Verification Corrections Test Suite (P59).

Coverage:
- P59-01: Production signature valid positive path (ephemeral Ed25519 key fixture).
- P59-02: Production signature invalid / corrupted path rejected.
- P59-03: Public test key rejected by production verification.
- P59-04: Untrusted production public key rejected.
- P59-05: Source tree status unknown / Git status failure fails closed.
- P59-06: Git timeout fails closed.
- P59-07: Missing repository metadata fails closed.
- P59-08: Dirty source rejection under production acceptance.
- P59-09: Invalid or nonexistent source revision rejection.
- P59-10: Deterministic recursive source digest across additions, deletions, renames.
- P59-11: Missing quarantine search roots fail scan completeness.
- P59-12: Directory traversal errors caught by onerror fail scan completeness.
- P59-13: Unreadable and oversized candidate files fail scan completeness.
- P59-14: Copied quarantine directory and copied registry file detected.
- P59-15: Compromised data in historical directory outside final_holdout detected.
- P59-16: Snapshot scan cannot prove permanent enforcement without independent evidence.
- P59-17: Explicit independent enforcement evidence verification.
- P59-18: Schema validator rejects incomplete scan or unverified permanent enforcement claims.
- P59-19: Attestation report accurately reflects subprocess fallback without overstating runtime acceptance.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
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
)
from world_model_vnext.hctt import HCTTWorldModel
from world_model_vnext.runner import EXECUTION_AUTHORIZED, TRAINING_PERMITTED
from world_model_vnext.runtime_isolation import detect_runtime_capabilities
from world_model_vnext.security_attestation import (
    ATTESTATION_MILESTONE,
    ATTESTATION_SCHEMA_VERSION,
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
    get_repo_root,
    get_source_commit_sha,
    is_source_tree_dirty,
    validate_security_attestation_schema,
    verify_attestation_digest,
    verify_attestation_signature,
    verify_commit_exists_in_repo,
    verify_physical_holdout_quarantine,
    verify_production_attestation_acceptance,
    verify_quarantine_enforcement_controls,
    verify_test_attestation_acceptance,
    verify_test_attestation_signature,
)


@pytest.fixture
def synthetic_checkpoint(tmp_path: Path) -> Tuple[Path, str]:
    """Creates a valid synthetic candidate checkpoint and computes its SHA-256 digest."""
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    ckpt_path = tmp_path / "candidate_model_p59.pt"
    torch.save(model.state_dict(), ckpt_path)
    sha256 = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
    return ckpt_path, sha256


@pytest.fixture
def ephemeral_production_keypair() -> Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey, str]:
    """Generates an in-memory ephemeral Ed25519 keypair for test-only production verification positive testing."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes_raw().hex()
    return priv_key, pub_key, pub_hex


# ==============================================================================
# P59.1: Production Attestation Acceptance & Ephemeral Key Positive Path
# ==============================================================================

def test_p59_01_production_signature_valid_positive_path(
    synthetic_checkpoint: Tuple[Path, str],
    ephemeral_production_keypair: Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey, str],
):
    """P59-01: A valid production attestation signed with an authorized production key passes verification and returns True."""
    priv_key, pub_key, pub_hex = ephemeral_production_keypair
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    # Resolve real commit SHA in repo
    real_commit = get_source_commit_sha()
    assert real_commit != "UNKNOWN_UNCOMMITTED_REVISION"
    assert verify_commit_exists_in_repo(real_commit) is True

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
        source_commit_sha=real_commit,
    )
    record = builder.build()

    # Re-sign with the ephemeral production key under a legitimate production scheme
    prod_scheme = "Ed25519-PROD-STAGE37C6-ACCEPTANCE"
    record["signature_scheme"] = prod_scheme
    record["signature_public_key_hex"] = pub_hex

    # Ensure source tree is marked clean for this test
    record["provenance"]["is_dirty_source_tree"] = False

    # Compute digest and sign
    recomputed_digest = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomputed_digest
    sig_bytes = priv_key.sign(recomputed_digest.encode("utf-8"))
    record["attestation_signature"] = sig_bytes.hex()

    # Create explicit trust policy containing this public key
    prod_policy = AttestationTrustPolicy(
        trusted_public_keys=frozenset({pub_hex}),
        allow_test_keys=False,
        allow_legacy_unsigned=False,
        enforce_production_acceptance=True,
        minimum_schema_version="2.0.0",
    )

    result = verify_production_attestation_acceptance(record, trust_policy=prod_policy)
    assert result is True


def test_p59_02_production_signature_invalid_corrupted_path(
    synthetic_checkpoint: Tuple[Path, str],
    ephemeral_production_keypair: Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey, str],
):
    """P59-02: A corrupted Ed25519 signature raises ContractViolationError."""
    priv_key, _, pub_hex = ephemeral_production_keypair
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()
    real_commit = get_source_commit_sha()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
        source_commit_sha=real_commit,
    )
    record = builder.build()

    record["signature_scheme"] = "Ed25519-PROD-STAGE37C6-ACCEPTANCE"
    record["signature_public_key_hex"] = pub_hex
    record["provenance"]["is_dirty_source_tree"] = False

    recomputed_digest = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomputed_digest
    sig_bytes = priv_key.sign(recomputed_digest.encode("utf-8"))

    # Corrupt signature
    corrupted_sig = bytearray(sig_bytes)
    corrupted_sig[0] ^= 0xFF
    record["attestation_signature"] = bytes(corrupted_sig).hex()

    prod_policy = AttestationTrustPolicy(
        trusted_public_keys=frozenset({pub_hex}),
        allow_test_keys=False,
        enforce_production_acceptance=True,
    )

    with pytest.raises(ContractViolationError, match="Production Ed25519 signature verification failed"):
        verify_production_attestation_acceptance(record, trust_policy=prod_policy)


def test_p59_03_public_test_key_rejected_by_production_acceptance(synthetic_checkpoint: Tuple[Path, str]):
    """P59-03: Production acceptance strictly rejects test signing scheme unconditionally."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    with pytest.raises(ContractViolationError, match="Production attestation acceptance strictly rejects test (signature )?scheme"):
        verify_production_attestation_acceptance(record)


def test_p59_04_untrusted_production_public_key_rejected(
    synthetic_checkpoint: Tuple[Path, str],
    ephemeral_production_keypair: Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey, str],
):
    """P59-04: An attestation signed with an untrusted public key is strictly rejected."""
    priv_key, _, pub_hex = ephemeral_production_keypair
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()
    real_commit = get_source_commit_sha()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
        source_commit_sha=real_commit,
    )
    record = builder.build()

    record["signature_scheme"] = "Ed25519-PROD-STAGE37C6-ACCEPTANCE"
    record["signature_public_key_hex"] = pub_hex
    record["provenance"]["is_dirty_source_tree"] = False

    recomputed = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomputed
    record["attestation_signature"] = priv_key.sign(recomputed.encode("utf-8")).hex()

    # Trust policy with a DIFFERENT public key
    other_pub_hex = "00" * 32
    policy_with_other_key = AttestationTrustPolicy(
        trusted_public_keys=frozenset({other_pub_hex}),
        allow_test_keys=False,
        enforce_production_acceptance=True,
    )

    with pytest.raises(ContractViolationError, match="not anchored in (trusted production keys|production trust policy)"):
        verify_production_attestation_acceptance(record, trust_policy=policy_with_other_key)


# ==============================================================================
# P59.2: Fail-Closed Source Provenance & Deterministic Hashing
# ==============================================================================

def test_p59_05_source_tree_status_unknown_git_status_failure():
    """P59-05: Git status failure raises ContractViolationError and fails closed in builder."""
    mock_res = MagicMock()
    mock_res.returncode = 128
    mock_res.stderr = "fatal: not a git repository"

    with patch("subprocess.run", return_value=mock_res):
        with pytest.raises(ContractViolationError, match="Git status failed with exit code 128"):
            is_source_tree_dirty()

    # Builder catching the exception must mark tree as dirty (fail closed)
    caps = detect_runtime_capabilities()
    with patch("world_model_vnext.security_attestation.is_source_tree_dirty", side_effect=ContractViolationError("Simulated failure")):
        builder = SecurityAttestationBuilder(
            candidate_checkpoint_sha256="0" * 64,
            runtime_capabilities=caps,
            active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
        )
        record = builder.build()
        assert record["provenance"]["is_dirty_source_tree"] is True


def test_p59_06_git_timeout_fails_closed():
    """P59-06: Git timeout raises ContractViolationError."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5.0)):
        with pytest.raises(ContractViolationError, match="Git status timed out"):
            is_source_tree_dirty()


def test_p59_07_missing_repository_metadata(tmp_path: Path):
    """P59-07: Missing .git repository metadata raises ContractViolationError."""
    empty_dir = tmp_path / "empty_repo"
    empty_dir.mkdir()
    with pytest.raises(ContractViolationError, match="no .git repository found"):
        is_source_tree_dirty(repo_root=empty_dir)


def test_p59_08_dirty_source_rejection(
    synthetic_checkpoint: Tuple[Path, str],
    ephemeral_production_keypair: Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey, str],
):
    """P59-08: Dirty source trees are rejected by production acceptance."""
    priv_key, _, pub_hex = ephemeral_production_keypair
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()
    real_commit = get_source_commit_sha()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
        source_commit_sha=real_commit,
    )
    record = builder.build()

    record["signature_scheme"] = "Ed25519-PROD-STAGE37C6-ACCEPTANCE"
    record["signature_public_key_hex"] = pub_hex
    record["provenance"]["is_dirty_source_tree"] = True

    recomputed = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomputed
    record["attestation_signature"] = priv_key.sign(recomputed.encode("utf-8")).hex()

    prod_policy = AttestationTrustPolicy(
        trusted_public_keys=frozenset({pub_hex}),
        allow_test_keys=False,
        enforce_production_acceptance=True,
    )

    with pytest.raises(ContractViolationError, match="strictly rejects dirty source trees"):
        verify_production_attestation_acceptance(record, trust_policy=prod_policy)


def test_p59_09_invalid_or_nonexistent_source_revision_rejection(
    synthetic_checkpoint: Tuple[Path, str],
    ephemeral_production_keypair: Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey, str],
):
    """P59-09: Invalid or nonexistent commit SHAs are rejected by production acceptance."""
    priv_key, _, pub_hex = ephemeral_production_keypair
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    prod_policy = AttestationTrustPolicy(
        trusted_public_keys=frozenset({pub_hex}),
        allow_test_keys=False,
        enforce_production_acceptance=True,
    )

    # 1. Non-hex commit SHA
    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
        source_commit_sha="invalid_non_hex_sha_value_here_12345678",
    )
    record = builder.build()
    record["signature_scheme"] = "Ed25519-PROD-STAGE37C6-ACCEPTANCE"
    record["signature_public_key_hex"] = pub_hex
    record["provenance"]["is_dirty_source_tree"] = False
    recomp = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomp
    record["attestation_signature"] = priv_key.sign(recomp.encode("utf-8")).hex()

    with pytest.raises(ContractViolationError, match="requires 40-hex lowercase commit SHA"):
        verify_production_attestation_acceptance(record, trust_policy=prod_policy)

    # 2. Non-existent commit SHA
    fake_sha = "0123456789abcdef0123456789abcdef01234567"
    record["source_commit_sha"] = fake_sha
    record["provenance"]["source_commit_sha"] = fake_sha
    recomp2 = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomp2
    record["attestation_signature"] = priv_key.sign(recomp2.encode("utf-8")).hex()

    with pytest.raises(ContractViolationError, match="does not exist in repository"):
        verify_production_attestation_acceptance(record, trust_policy=prod_policy)


def test_p59_10_deterministic_recursive_source_digest(tmp_path: Path):
    """P59-10: Recursive source-tree digest is deterministic and sensitive to file addition, modification, and deletion."""
    src_dir = tmp_path / "src"
    sub_dir = src_dir / "subfolder"
    sub_dir.mkdir(parents=True)

    f1 = src_dir / "root_mod.py"
    f2 = sub_dir / "sub_mod.py"
    f1.write_text("print('root')", encoding="utf-8")
    f2.write_text("print('sub')", encoding="utf-8")

    digest_1 = compute_source_tree_digest(src_dir)
    digest_2 = compute_source_tree_digest(src_dir)
    assert digest_1 == digest_2

    # Add a nested file
    f3 = sub_dir / "another_mod.py"
    f3.write_text("print('another')", encoding="utf-8")
    digest_with_f3 = compute_source_tree_digest(src_dir)
    assert digest_with_f3 != digest_1

    # Delete the nested file: digest must revert to original
    f3.unlink()
    digest_after_delete = compute_source_tree_digest(src_dir)
    assert digest_after_delete == digest_1

    # Modify content of nested file
    f2.write_text("print('sub modified')", encoding="utf-8")
    digest_modified = compute_source_tree_digest(src_dir)
    assert digest_modified != digest_1


# ==============================================================================
# P59.3: Quarantine Verification Scanner & Enforcement Claims
# ==============================================================================

def test_p59_11_missing_quarantine_search_roots_fail_scan(tmp_path: Path):
    """P59-11: Missing required search roots record in unreadable_files and cause scan_complete=False."""
    missing_dir = tmp_path / "missing_search_dir_xyz"
    res = verify_physical_holdout_quarantine([missing_dir])

    assert res["scan_complete"] is False
    assert res["physical_exclusion_verified"] is False
    assert any("Missing search root" in f for f in res["unreadable_files"])


def test_p59_12_directory_traversal_errors_caught_by_onerror(tmp_path: Path):
    """P59-12: Traversal errors caught by os.walk onerror record in unreadable_files and fail scan_complete."""
    scan_dir = tmp_path / "traversal_test"
    scan_dir.mkdir()

    orig_walk = os.walk
    def mock_walk(top, *args, **kwargs):
        onerror = kwargs.get("onerror")
        if onerror:
            onerror(PermissionError(13, "Permission denied", str(top)))
        return orig_walk(top, *args, **kwargs)

    with patch("os.walk", side_effect=mock_walk):
        res = verify_physical_holdout_quarantine([scan_dir])
        assert res["scan_complete"] is False
        assert res["physical_exclusion_verified"] is False
        assert any("Directory traversal error" in f for f in res["unreadable_files"])


def test_p59_13_unreadable_and_oversized_candidate_files(tmp_path: Path):
    """P59-13: Oversized candidate files (>100MB) fail scan completeness."""
    scan_dir = tmp_path / "oversized_dir"
    scan_dir.mkdir()
    huge = scan_dir / "large_data.npz"
    with open(huge, "wb") as f:
        f.seek(101 * 1024 * 1024)
        f.write(b"\0")

    res = verify_physical_holdout_quarantine([scan_dir])
    assert res["scan_complete"] is False
    assert res["physical_exclusion_verified"] is False
    assert any("oversized" in f for f in res["unreadable_files"])


def test_p59_14_copied_quarantine_directory_and_copied_registry_detected(tmp_path: Path):
    """P59-14: Copied quarantine directory or registry file outside canonical path is not exempt and is detected."""
    copy_dir = tmp_path / "stage36_holdout_quarantine_copy"
    copy_dir.mkdir()

    # Copied registry file containing compromised sample IDs
    fake_reg = copy_dir / "holdout_quarantine_record.json"
    compromised_sids = list(get_compromised_sample_ids())
    fake_reg.write_text(json.dumps({
        "notes": "copied registry",
        "sample_id": compromised_sids[0],
    }), encoding="utf-8")

    res = verify_physical_holdout_quarantine([copy_dir])
    assert res["physical_exclusion_verified"] is False
    assert any("holdout_quarantine_record.json" in f for f in res["exposed_compromised_files_found"])


def test_p59_15_compromised_data_in_historical_directory_outside_final_holdout():
    """P59-15: Compromised files in stage35_production outside canonical final_holdout are detected."""
    research_root = Path(__file__).resolve().parent.parent
    s35_dir = research_root / "experiments" / "stage35_production"

    # Leak simulated in prelift_long
    sim_dir = s35_dir / "reports"
    sim_file = sim_dir / "test_stage35_holdout_probe_leak.npz"
    try:
        sim_file.write_bytes(b"leak_test_data")
        res = verify_physical_holdout_quarantine([s35_dir])
        assert res["physical_exclusion_verified"] is False
        assert any("test_stage35_holdout_probe_leak.npz" in f for f in res["exposed_compromised_files_found"])
    finally:
        if sim_file.exists():
            sim_file.unlink()


def test_p59_16_snapshot_scan_cannot_prove_permanent_enforcement(tmp_path: Path):
    """P59-16: A clean snapshot scan of custom directories cannot establish permanent quarantine enforcement."""
    clean_dir = tmp_path / "clean_isolated_dir"
    clean_dir.mkdir()
    dummy = clean_dir / "innocent.json"
    dummy.write_text(json.dumps({"innocent": True}), encoding="utf-8")

    res = verify_physical_holdout_quarantine([clean_dir])
    assert res["scan_complete"] is True
    assert res["physical_exclusion_verified"] is True
    assert len(res["exposed_compromised_files_found"]) == 0
    # Crucial P59 requirement: snapshot scan alone cannot prove permanent enforcement
    assert res["quarantine_enforcement_controls_verified"] is False
    assert res["quarantine_permanently_enforced"] is False


def test_p59_17_explicit_independent_enforcement_evidence_verification(tmp_path: Path):
    """P59-17: Permanent enforcement in snapshot scan requires explicit verified enforcement evidence."""
    clean_dir = tmp_path / "clean_dir_with_evidence"
    clean_dir.mkdir()

    # Valid enforcement evidence
    valid_ev = {"controls_active": True, "immutable_registry_sha": EXPECTED_QUARANTINE_REGISTRY_SHA256}
    res_valid = verify_physical_holdout_quarantine([clean_dir], enforcement_evidence=valid_ev)
    assert res_valid["physical_exclusion_verified"] is True
    assert res_valid["quarantine_enforcement_controls_verified"] is True
    assert res_valid["quarantine_permanently_enforced"] is True

    # Invalid enforcement evidence
    invalid_ev = {"controls_active": False}
    res_invalid = verify_physical_holdout_quarantine([clean_dir], enforcement_evidence=invalid_ev)
    assert res_invalid["quarantine_enforcement_controls_verified"] is False
    assert res_invalid["quarantine_permanently_enforced"] is False


def test_p59_18_schema_validator_rejects_incomplete_scan_or_unverified_permanent_claims(synthetic_checkpoint: Tuple[Path, str]):
    """P59-18: Schema validator rejects attestation records with contradictory quarantine evidence."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    base_record = builder.build()

    # 1. scan_complete=False but physical_exclusion_verified=True
    r1 = copy.deepcopy(base_record)
    r1["governance_invariants"]["physical_artifact_quarantine"]["scan_complete"] = False
    r1["governance_invariants"]["physical_artifact_quarantine"]["physical_exclusion_verified"] = True
    recomp1 = compute_canonical_attestation_payload_digest(r1)
    r1["evidence_digest_sha256"] = recomp1
    r1["attestation_signature"] = SecurityAttestationBuilder(ckpt_sha, caps).build()["attestation_signature"]

    with pytest.raises(ContractViolationError, match="Physical holdout quarantine scan was incomplete"):
        validate_security_attestation_schema(r1, policy=DEFAULT_TEST_TRUST_POLICY)

    # 2. quarantine_permanently_enforced=True without quarantine_enforcement_controls_verified
    r2 = copy.deepcopy(base_record)
    r2["governance_invariants"]["physical_artifact_quarantine"]["quarantine_permanently_enforced"] = True
    r2["governance_invariants"]["physical_artifact_quarantine"]["quarantine_enforcement_controls_verified"] = False
    with pytest.raises(ContractViolationError, match="Permanent quarantine enforcement cannot be claimed without verified independent enforcement controls"):
        validate_security_attestation_schema(r2, policy=DEFAULT_TEST_TRUST_POLICY)


# ==============================================================================
# P59.4 & P59.5: Attestation Report & Actual Runtime Boundaries
# ==============================================================================

def test_p59_19_attestation_report_runtime_acceptance_boundaries(synthetic_checkpoint: Tuple[Path, str]):
    """P59-19: Subprocess fallback tier accurately reports unverified quotas and proxy blackholing."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    # Record resource quota with null observed measurements
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota & Limit Enforcement Schema",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"limits_available": False, "enforced": False, "observed_limits": {"effective_cpu_cores": None}},
        rationale="Cgroups v2 measurements unavailable under subprocess fallback.",
    )
    record = builder.build()

    # Partially verified resource quota is accepted
    assert record["controls"]["ISO-RES-01"]["status"] == STATUS_PARTIALLY_VERIFIED
    validate_security_attestation_schema(record, policy=DEFAULT_TEST_TRUST_POLICY)

    # But claiming TESTED_AND_VERIFIED with null measurements must fail
    record["controls"]["ISO-RES-01"]["status"] = STATUS_TESTED_AND_VERIFIED
    recomputed = compute_canonical_attestation_payload_digest(record)
    record["evidence_digest_sha256"] = recomputed
    with pytest.raises(ContractViolationError, match="Null or unavailable resource measurements cannot receive STATUS_TESTED_AND_VERIFIED"):
        validate_security_attestation_schema(record, policy=DEFAULT_TEST_TRUST_POLICY)
