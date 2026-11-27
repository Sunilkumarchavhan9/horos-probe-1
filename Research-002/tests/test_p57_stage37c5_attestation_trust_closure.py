"""HOROS PROBE-1 — Stage 3.7C.5
Security Attestation Trust Closure & Runtime Backend Acceptance Test Suite (P57).

Mandatory Verification Requirements:
1. Signature deletion and legacy digest substitution must fail closed.
2. Signature tampering and untrusted public-key substitution must fail closed.
3. Top-level and provenance source revision metadata modification must be rejected.
4. Invalid or breached governance policy values must fail closed.
5. Missing, null, or unenforced cgroup limits cannot receive verified status.
6. Unknown, subprocess, or mismatched isolation tiers cannot receive verified network isolation.
7. Docker OCI checkpoint mount resolution separates host and worker paths.
8. WSL2 worker executable resolution uses python3 and translates Windows drive paths to /mnt/<drive>/.
9. Explicitly requested isolation tiers fail closed if unavailable (never silent fallback).
10. Nested, renamed, and hash-matched compromised holdout artifacts are detected recursively.
11. Procfs /proc/1/root route probes the specific protected target, not merely root directory existence.
12. Authentic attestation records verify completely; all tampered variations fail closed.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock, patch

import pytest
import torch
from cryptography.hazmat.primitives.asymmetric import ed25519

from world_model_vnext.contracts import (
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_PROTOCOL,
    COMPROMISED_HOLDOUT_STATUS,
    ContractViolationError,
    LOCKED_HCTT_CONFIG,
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
    STATUS_BLOCKED,
    STATUS_IMPLEMENTED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_TESTED_AND_VERIFIED,
    TEST_NONPROD_PUBLIC_KEY_HEX,
    SecurityAttestationBuilder,
    compute_evidence_digest,
    get_source_commit_sha,
    save_security_attestation,
    sign_attestation_digest,
    validate_security_attestation_schema,
    verify_attestation_digest,
    verify_attestation_signature,
    verify_physical_holdout_quarantine,
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
    ckpt_path = tmp_path / "candidate_model.pt"
    torch.save(model.state_dict(), ckpt_path)
    sha256 = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
    return ckpt_path, sha256


def test_01_signature_deletion_and_legacy_digest_substitution_rejection(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that signature deletion with legacy 3-parameter digest substitution is strictly rejected."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-FS-01",
        name="Filesystem Isolation",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"root_readonly": True},
        rationale="Tested partial isolation.",
    )
    record = builder.build()
    assert verify_attestation_signature(record) is True
    assert verify_attestation_digest(record) is True

    # Attack: delete signature and replace digest with legacy 3-parameter digest
    tampered = copy.deepcopy(record)
    del tampered["attestation_signature"]

    legacy_digest = compute_evidence_digest(
        controls=tampered["controls"],
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
    )
    tampered["evidence_digest_sha256"] = legacy_digest

    assert verify_attestation_signature(tampered) is False
    assert verify_attestation_digest(tampered) is False
    with pytest.raises(ContractViolationError, match="requires a mandatory Ed25519 signature"):
        validate_security_attestation_schema(tampered)


def test_02_signature_tampering_and_public_key_substitution(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that tampering with signatures or substituting attacker public keys fails closed."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-IPC-01",
        name="IPC Validation",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"pickle_rejected": True},
        rationale="Pickle rejected.",
    )
    record = builder.build()

    # 1. Attacker generates their own keypair and signs the digest
    attacker_key = ed25519.Ed25519PrivateKey.generate()
    attacker_pub_hex = attacker_key.public_key().public_bytes_raw().hex()
    attacker_sig = attacker_key.sign(record["evidence_digest_sha256"].encode("utf-8")).hex()

    sub_key_record = copy.deepcopy(record)
    sub_key_record["signature_public_key_hex"] = attacker_pub_hex
    sub_key_record["attestation_signature"] = attacker_sig

    assert verify_attestation_signature(sub_key_record) is False
    with pytest.raises(ContractViolationError, match="Untrusted public key"):
        validate_security_attestation_schema(sub_key_record)

    # 2. Corrupt signature bytes
    corrupt_sig_record = copy.deepcopy(record)
    sig_chars = list(corrupt_sig_record["attestation_signature"])
    sig_chars[0] = "0" if sig_chars[0] != "0" else "1"
    corrupt_sig_record["attestation_signature"] = "".join(sig_chars)

    assert verify_attestation_signature(corrupt_sig_record) is False
    with pytest.raises(ContractViolationError, match="signature verification failed"):
        validate_security_attestation_schema(corrupt_sig_record)


def test_03_source_revision_metadata_modification(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that top-level vs provenance source revision mismatch invalidates the attestation."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-GOV-01",
        name="Governance",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"verified": True},
        rationale="Governance verified.",
    )
    record = builder.build()

    # Modify top-level source_commit_sha only
    tampered_top = copy.deepcopy(record)
    tampered_top["source_commit_sha"] = "a" * 40
    assert verify_attestation_signature(tampered_top) is False
    with pytest.raises(ContractViolationError, match="source_commit_sha does not match"):
        validate_security_attestation_schema(tampered_top)

    # Modify provenance source_commit_sha only
    tampered_prov = copy.deepcopy(record)
    tampered_prov["provenance"]["source_commit_sha"] = "b" * 40
    assert verify_attestation_signature(tampered_prov) is False
    with pytest.raises(ContractViolationError, match="source_commit_sha does not match"):
        validate_security_attestation_schema(tampered_prov)


def test_04_invalid_governance_policy_values_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that any non-compliant governance policy value fails closed."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    record = builder.build()

    # Test breach of each governance field
    test_cases = [
        ("training_permitted", 1, "training_permitted != 0"),
        ("execution_authorized", 1, "execution_authorized != 0"),
        ("gradient_steps", 10, "gradient_steps != 0"),
        ("optimizer_updates", 5, "optimizer_updates != 0"),
        ("new_holdout_generation", "AUTHORIZED", "new_holdout_generation != NOT_AUTHORIZED"),
        ("final_evaluation", "PERMITTED", "final_evaluation != BLOCKED"),
        ("production_keys", "GENERATED", "production_keys != NOT_GENERATED"),
        ("production_vault", "INITIALIZED", "production_vault != NOT_INITIALIZED"),
    ]

    for key, bad_val, match_msg in test_cases:
        tampered = copy.deepcopy(record)
        tampered["governance_invariants"][key] = bad_val
        if key in tampered["governance_invariants"].get("runtime_verified_invariants", {}):
            tampered["governance_invariants"]["runtime_verified_invariants"][key] = bad_val
        if key in tampered["governance_invariants"].get("static_policy_declarations", {}):
            tampered["governance_invariants"]["static_policy_declarations"][key] = bad_val
        if key in tampered["governance_invariants"].get("production_authorization_state", {}):
            tampered["governance_invariants"]["production_authorization_state"][key] = bad_val

        # Recalculate digest and signature to test semantic governance validation independently
        new_dig = compute_evidence_digest(
            controls=tampered["controls"],
            candidate_checkpoint_sha256=ckpt_sha,
            runtime_capabilities=caps,
            active_runtime_identity=tampered.get("active_runtime_identity"),
            evaluator_context=tampered.get("evaluator_context"),
            governance_invariants=tampered["governance_invariants"],
            provenance=tampered.get("provenance"),
        )
        tampered["evidence_digest_sha256"] = new_dig
        tampered["attestation_signature"] = sign_attestation_digest(new_dig)

        with pytest.raises(ContractViolationError, match=match_msg):
            validate_security_attestation_schema(tampered)


def test_05_missing_or_unenforced_cgroup_limits(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that ISO-RES-01 cannot be marked STATUS_TESTED_AND_VERIFIED with unenforced or over-quota limits."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )

    # 1. limits not actively enforced
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "limits_available": True,
            "enforced": False,  # Unenforced
            "observed_limits": {"effective_cpu_cores": 2.0, "memory_max_bytes": 2147483648, "pids_max": 128},
        },
    )
    with pytest.raises(ContractViolationError, match="limits are not actively enforced"):
        validate_security_attestation_schema(builder.build())

    # 2. limits unavailable
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "limits_available": False,
            "enforced": True,
            "observed_limits": {"effective_cpu_cores": 2.0, "memory_max_bytes": 2147483648, "pids_max": 128},
        },
    )
    with pytest.raises(ContractViolationError, match="limits_available is False"):
        validate_security_attestation_schema(builder.build())

    # 3. CPU quota exceeds 2.0 cores
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


def test_06_unknown_and_mismatched_isolation_tiers(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that ISO-NET-01 strictly requires a recognized actual isolation tier."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    # Case A: subprocess_fallback cannot receive verified network isolation
    builder_sub = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": PROFILE_SUBPROCESS_FALLBACK},
    )
    builder_sub.record_control(
        control_id="ISO-NET-01",
        name="Network Isolation",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"active_isolation_tier": PROFILE_SUBPROCESS_FALLBACK, "host_isolation_supported": True},
    )
    with pytest.raises(ContractViolationError, match="Network capability availability cannot substitute"):
        validate_security_attestation_schema(builder_sub.build())

    # Case B: tier is None / omitted
    builder_none = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={},
    )
    builder_none.record_control(
        control_id="ISO-NET-01",
        name="Network Isolation",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"host_isolation_supported": True},
    )
    with pytest.raises(ContractViolationError, match="Network capability availability cannot substitute"):
        validate_security_attestation_schema(builder_none.build())


def test_07_docker_checkpoint_mount_resolution(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies Docker OCI execution command separates host checkpoint paths from container /model/checkpoint.pt."""
    ckpt_path, _ = synthetic_checkpoint

    # 1. Command with checkpoint
    cmd_with_ckpt = build_oci_run_command(
        checkpoint_path=ckpt_path,
        image_name="test-image:tag",
    )
    assert "--network" in cmd_with_ckpt and "none" in cmd_with_ckpt
    assert "--read-only" in cmd_with_ckpt
    assert any("/model/checkpoint.pt:ro" in arg for arg in cmd_with_ckpt)

    # 2. Command without checkpoint (checkpoint-free security probes)
    cmd_no_ckpt = build_oci_run_command(
        checkpoint_path=None,
        image_name="test-image:tag",
    )
    assert "--network" in cmd_no_ckpt and "none" in cmd_no_ckpt
    assert "--read-only" in cmd_no_ckpt
    assert not any("/model/checkpoint.pt" in arg for arg in cmd_no_ckpt)


def test_08_wsl2_worker_executable_and_mount_resolution():
    """Verifies WSL2 command generation and Windows to WSL mount path translation."""
    # 1. Path translation
    wsl_p1 = translate_to_wsl_path("C:\\projects\\horos-probe-1\\model.pt")
    assert wsl_p1 == "/mnt/c/projects/horos-probe-1/model.pt"

    wsl_p2 = translate_to_wsl_path("D:\\research\\vault\\targets.json")
    assert wsl_p2 == "/mnt/d/research/vault/targets.json"

    posix_path = translate_to_wsl_path("/tmp/test_dir/file.pt")
    assert posix_path == "/tmp/test_dir/file.pt"

    # 2. WSL namespace command uses Linux python3
    wsl_cmd = build_namespace_run_command(use_wsl=True)
    assert wsl_cmd[0] == "wsl"
    assert "unshare" in wsl_cmd
    assert "python3" in wsl_cmd


def test_09_fail_closed_unsupported_runtime_profile():
    """Verifies that requesting an unavailable runtime tier fails closed rather than silent fallback."""
    with patch("world_model_vnext.worker_isolation.detect_runtime_capabilities") as mock_caps:
        mock_caps.return_value = {
            "docker_available": False,
            "podman_available": False,
            "wsl_unshare_available": False,
            "native_unshare_available": False,
        }

        # Requesting docker_oci without docker must raise ContractViolationError
        with pytest.raises(ContractViolationError, match="Docker/Podman engine not available"):
            IsolatedInferenceWorker(execution_profile=PROFILE_DOCKER_OCI)

        # Requesting wsl2_namespaces without WSL unshare must raise ContractViolationError
        with pytest.raises(ContractViolationError, match="WSL2 unshare not available"):
            IsolatedInferenceWorker(execution_profile=PROFILE_WSL2_NAMESPACES)


def test_10_nested_and_renamed_compromised_holdout_artifacts(tmp_path: Path):
    """Verifies that recursive scan discovers nested and renamed compromised holdout artifacts."""
    # Create nested directories with a compromised holdout file
    nested_dir = tmp_path / "data" / "nested" / "subfolder"
    nested_dir.mkdir(parents=True)
    compromised_file = nested_dir / "stage35_final_holdout_renamed.npz"
    compromised_file.write_bytes(b"dummy_compromised_data")

    # Patch eval_dirs to include tmp_path
    with patch("world_model_vnext.security_attestation.Path") as mock_path:
        def mock_path_constructor(p):
            if str(p) in ("Research-002/data", "data", "Research-002/experiments", "experiments"):
                return tmp_path / "data"
            return Path(p)
        mock_path.side_effect = mock_path_constructor

        scan_result = verify_physical_holdout_quarantine()
        assert scan_result["physical_exclusion_verified"] is False
        assert scan_result["quarantine_permanently_enforced"] is False
        assert len(scan_result["exposed_compromised_files_found"]) > 0


def test_11_actual_protected_target_access_through_procfs(tmp_path: Path):
    """Verifies that /proc/1/root route only reports ACCESSIBLE if the sensitive vault target is reached."""
    secret_vault = tmp_path / "private_vault"
    secret_vault.mkdir()
    secret_file = secret_vault / "protected_targets.json"
    secret_file.write_text(json.dumps({"secret_vector": [1, 2, 3]}), encoding="utf-8")
    apply_os_vault_isolation(secret_vault)

    worker = IsolatedInferenceWorker(working_dir=tmp_path)
    try:
        vault_res = worker.test_vault_isolation(secret_vault)
        assert vault_res.get("status") == "OK"
        # Proc1 root must NOT be ACCESSIBLE unless the protected target was actually read
        proc1_route = vault_res.get("routes", {}).get("proc_1_root_access", {})
        assert proc1_route.get("status") in ("NOT_FOUND", "UNSUPPORTED", "DENIED")
        assert vault_res.get("any_route_accessible") is False
    finally:
        worker.close()
        remove_os_vault_isolation(secret_vault)


def test_12_complete_attestation_authenticity_verification(synthetic_checkpoint: Tuple[Path, str], tmp_path: Path):
    """Verifies end-to-end attestation creation, serialization, deserialization, and signature verification."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-REPLAY-01",
        name="Replay Protection",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"client_replay_rejected": True, "worker_replay_rejected": True},
        rationale="Tested client and worker replay denial.",
    )
    record = builder.build()

    # Verify memory representation
    assert verify_attestation_signature(record) is True
    assert verify_attestation_digest(record) is True

    # Save to disk and verify round-trip
    report_file = tmp_path / "security_attestation_report.json"
    save_security_attestation(record, report_file)

    loaded = json.loads(report_file.read_text(encoding="utf-8"))
    assert verify_attestation_signature(loaded) is True
    assert verify_attestation_digest(loaded) is True
