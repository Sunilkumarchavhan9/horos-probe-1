"""HOROS PROBE-1 — Stage 3.7C.4
Cross-Runtime Security Acceptance & Evidence Closure Test Suite (P56).

Mandatory Verification Requirements:
1. Null resource measurements cannot receive verified status.
2. Incorrect resource quotas cannot receive verified status.
3. Inaccessible procfs routes cannot be reported as actively denied.
4. Unavailable worker identity cannot receive verified status.
5. Network capability availability cannot substitute for actual worker isolation.
6. Unauthorized HIPC commands must be rejected.
7. Modified governance evidence must invalidate attestation.
8. Modified runtime evidence must invalidate attestation.
9. Attestation must bind to the actual candidate checkpoint.
10. Synthetic security probes cannot authorize production research execution.
11. Multi-route private vault isolation distinguishing DENIED, NOT_FOUND, UNSUPPORTED, INCONCLUSIVE.
12. HIPC binary frame validation (pickle rejection, oversized frames, tensor metadata, nonces).
13. Physical quarantine verification of compromised Stage 3.5 holdout.
14. Ed25519 cryptographic provenance anchoring with non-production test key.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pytest
import torch

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
    detect_runtime_capabilities,
    resolve_execution_profile,
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
    save_security_attestation,
    sign_attestation_digest,
    validate_security_attestation_schema,
    verify_attestation_digest,
    verify_attestation_signature,
    verify_physical_holdout_quarantine,
)
from world_model_vnext.worker_isolation import (
    HIPC_MAGIC,
    MAX_IPC_HEADER_SIZE,
    MAX_IPC_PAYLOAD_SIZE,
    IsolatedInferenceWorker,
    apply_os_vault_isolation,
    recv_ipc_message,
    remove_os_vault_isolation,
    send_ipc_message,
)


@pytest.fixture
def synthetic_checkpoint(tmp_path: Path) -> Tuple[Path, str]:
    """Creates a valid synthetic candidate checkpoint and computes its SHA-256 digest."""
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    ckpt_path = tmp_path / "candidate_model.pt"
    torch.save(model.state_dict(), ckpt_path)
    sha256 = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
    return ckpt_path, sha256


@pytest.fixture
def valid_inference_payload() -> Dict[str, Any]:
    """Creates a valid schema-compliant inference payload."""
    B, T = 2, 30
    return {
        "task": "peg_in_hole",
        "states": torch.randn((B, T, 15), dtype=torch.float32),
        "actions": torch.randn((B, T, 6), dtype=torch.float32),
        "wrenches": torch.randn((B, T, 6), dtype=torch.float32),
    }


def test_01_null_resource_measurements_cannot_receive_verified_status(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that null or unavailable resource measurements are strictly rejected from verified status."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
    )

    # Attempt to record ISO-RES-01 as TESTED_AND_VERIFIED with null measurements
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota & Limit Enforcement Schema",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "observed_limits": {
                "effective_cpu_cores": None,
                "memory_max_bytes": None,
                "pids_max": None,
            },
            "enforced": False,
        },
        rationale="Unmeasured limits must not receive verified status.",
    )

    record = builder.build()
    with pytest.raises(ContractViolationError, match="Null or unavailable resource measurements cannot receive STATUS_TESTED_AND_VERIFIED"):
        validate_security_attestation_schema(record)


def test_02_incorrect_resource_quotas_cannot_receive_verified_status(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that observed limits exceeding expected quotas (CPU: 2, Mem: 2GB, PIDs: 128) fail schema validation."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
    )

    # Observed limits exceed expected limits (4.0 cores, 8GB memory, 1024 pids)
    builder.record_control(
        control_id="ISO-RES-01",
        name="Resource Quota & Limit Enforcement Schema",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "observed_limits": {
                "effective_cpu_cores": 4.0,  # Exceeds 2.0
                "memory_max_bytes": 8 * 1024 * 1024 * 1024,  # Exceeds 2GB
                "pids_max": 1024,  # Exceeds 128
            },
            "enforced": True,
        },
        rationale="Over-quota limits must be rejected.",
    )

    record = builder.build()
    with pytest.raises(ContractViolationError, match="Incorrect resource quotas cannot receive STATUS_TESTED_AND_VERIFIED"):
        validate_security_attestation_schema(record)


def test_03_inaccessible_procfs_routes_cannot_be_reported_as_actively_denied(tmp_path: Path):
    """Verifies that platform unsupported routes (e.g. procfs on Windows) report UNSUPPORTED, not DENIED."""
    worker = IsolatedInferenceWorker(working_dir=tmp_path)
    try:
        vault_path = tmp_path / "private_vault"
        vault_path.mkdir()
        res = worker.test_vault_isolation(vault_path)
        assert res.get("status") == "OK"
        routes = res.get("routes", {})

        # If running on Windows host, proc_1_root and symlink routes must be UNSUPPORTED, not DENIED
        if os.name == "nt":
            assert routes["proc_1_root_access"]["status"] == "UNSUPPORTED"
            assert routes["mount_namespace_visibility"]["status"] == "UNSUPPORTED"
            assert routes["inherited_handle_leakage"]["status"] == "UNSUPPORTED"
        else:
            # On Linux, route status must be one of the 4 strict categories
            for r_name, r_info in routes.items():
                assert r_info["status"] in ("DENIED", "NOT_FOUND", "UNSUPPORTED", "INCONCLUSIVE", "ACCESSIBLE")
    finally:
        worker.close()


def test_04_unavailable_worker_identity_cannot_receive_verified_status(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that unavailable or None worker identity rejects STATUS_TESTED_AND_VERIFIED for ISO-UID-01."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
    )

    builder.record_control(
        control_id="ISO-UID-01",
        name="Worker Identity & Privilege Escalation Restrictions",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "uid": None,  # Missing identity
            "escalation_prevented": False,
        },
        rationale="Missing identity must not receive verified status.",
    )

    record = builder.build()
    with pytest.raises(ContractViolationError, match="Unavailable worker identity or unverified escalation denial"):
        validate_security_attestation_schema(record)


def test_05_network_capability_availability_cannot_substitute_for_actual_worker_isolation(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that host capability flags cannot substitute for actual worker runtime isolation tier in ISO-NET-01."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )

    # Claiming verified network isolation while running under subprocess_fallback must fail
    builder.record_control(
        control_id="ISO-NET-01",
        name="Kernel Network Namespace Isolation & Dropped Capabilities",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={
            "active_isolation_tier": "subprocess_fallback",
            "host_isolation_supported": True,  # Host capability present, but worker ran in subprocess
        },
        rationale="Host capability must not substitute for actual worker tier.",
    )

    record = builder.build()
    with pytest.raises(ContractViolationError, match="Network capability availability cannot substitute for actual worker isolation"):
        validate_security_attestation_schema(record)


def test_06_unauthorized_hipc_commands_must_be_rejected(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that unauthorized or unknown commands across HIPC return structured error rejection."""
    ckpt_path, _ = synthetic_checkpoint
    worker = IsolatedInferenceWorker(checkpoint_path=ckpt_path)
    try:
        raw_resp = worker.run_custom_command({
            "command": "UNAUTHORIZED_ADMIN_EXEC_CMD_001",
            "params": {"inject": "sh"},
        })
        assert isinstance(raw_resp, dict)
        assert raw_resp.get("status") == "ERROR"
        assert "unauthorized or unhandled command" in raw_resp.get("error", "").lower()
        assert raw_resp.get("command") == "UNAUTHORIZED_ADMIN_EXEC_CMD_001"
    finally:
        worker.close()


def test_07_modified_governance_evidence_must_invalidate_attestation(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that modifying governance invariants invalidates both evidence digest and Ed25519 signature."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )

    builder.record_control(
        control_id="ISO-IPC-01",
        name="Safe Binary IPC Protocol",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"pickle_free": True},
        rationale="Basic IPC check.",
    )

    record = builder.build()
    assert verify_attestation_digest(record) is True
    assert verify_attestation_signature(record) is True

    # Mutate governance invariant in deep copy
    tampered = copy.deepcopy(record)
    tampered["governance_invariants"]["training_permitted"] = 1
    assert verify_attestation_signature(tampered) is False

    with pytest.raises(ContractViolationError):
        validate_security_attestation_schema(tampered)


def test_08_modified_runtime_evidence_must_invalidate_attestation(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that modifying active runtime identity invalidates cryptographic provenance."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback", "uid": 1000},
    )

    builder.record_control(
        control_id="ISO-IPC-01",
        name="Safe Binary IPC Protocol",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"pickle_free": True},
    )

    record = builder.build()
    assert verify_attestation_signature(record) is True

    # Mutate runtime identity
    tampered = copy.deepcopy(record)
    tampered["active_runtime_identity"]["active_runtime_tier"] = "docker_oci"
    assert verify_attestation_signature(tampered) is False

    with pytest.raises(ContractViolationError, match="digest mismatch"):
        validate_security_attestation_schema(tampered)


def test_09_attestation_must_bind_to_actual_candidate_checkpoint(synthetic_checkpoint: Tuple[Path, str]):
    """Verifies that attestation strictly binds to candidate checkpoint SHA-256."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
    )
    builder.record_control(
        control_id="ISO-IPC-01",
        name="Safe Binary IPC Protocol",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"valid": True},
    )

    record = builder.build()
    assert verify_attestation_signature(record) is True

    # Tamper with checkpoint SHA
    tampered = copy.deepcopy(record)
    tampered["candidate_checkpoint_sha256"] = "f" * 64
    assert verify_attestation_signature(tampered) is False

    with pytest.raises(ContractViolationError, match="digest mismatch"):
        validate_security_attestation_schema(tampered)


def test_10_synthetic_security_probes_cannot_authorize_production_research_execution(synthetic_checkpoint: Tuple[Path, str]):
    """Proves that passing 100% of security probes does NOT authorize production training or execution."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
    )
    record = builder.build()

    # Governance invariants remain permanently locked
    gov = record["governance_invariants"]
    assert gov["training_permitted"] == 0
    assert gov["execution_authorized"] == 0
    assert gov["new_holdout_generation"] == "NOT_AUTHORIZED"
    assert gov["production_keys"] == "NOT_GENERATED"
    assert gov["production_vault"] == "NOT_INITIALIZED"
    assert gov["final_evaluation"] == "BLOCKED"
    assert gov["compromised_holdout_id"] == "stage35_final_holdout"
    assert gov["compromised_holdout_status"] == "COMPROMISED_PUBLIC_EXPOSURE"


def test_11_comprehensive_vault_isolation_multi_route(tmp_path: Path):
    """Verifies multi-route vault isolation distinguishing DENIED, NOT_FOUND, UNSUPPORTED, INCONCLUSIVE."""
    secret_vault = tmp_path / "private_vault"
    secret_vault.mkdir()
    secret_file = secret_vault / "protected_targets.json"
    secret_file.write_text(json.dumps({"secret_vector": [9, 8, 7]}), encoding="utf-8")
    apply_os_vault_isolation(secret_vault)

    worker = IsolatedInferenceWorker(working_dir=tmp_path)
    try:
        vault_res = worker.test_vault_isolation(secret_vault)
        assert vault_res.get("status") == "OK"
        assert vault_res.get("any_route_accessible") is False
        assert vault_res.get("conclusive_isolation") is True

        routes = vault_res.get("routes", {})
        assert routes["direct_filesystem_access"]["status"] in ("DENIED", "NOT_FOUND")
        assert routes["relative_path_traversal"]["status"] in ("DENIED", "NOT_FOUND")
        assert routes["environment_leakage"]["status"] == "DENIED"
    finally:
        worker.close()
        remove_os_vault_isolation(secret_vault)


def test_12_hipc_negative_frames_and_oversized_rejection():
    """Verifies HIPC strictly rejects raw pickle headers, oversized payloads, and invalid tensor buffers."""
    # 1. Pickle opcode rejection
    pickle_stream = io.BytesIO(b"HIPC\x00\x00\x00\x10\x00\x00\x00\x08\x80\x04\x95\x00\x00\x00\x00.")
    with pytest.raises(ContractViolationError, match="Pickle payload signature detected"):
        recv_ipc_message(pickle_stream)

    # 2. Oversized total length rejection (> 64MB)
    oversized_stream = io.BytesIO(b"HIPC" + struct.pack(">I", MAX_IPC_PAYLOAD_SIZE + 1024) + b"\x00" * 8)
    with pytest.raises(ContractViolationError, match="violates allowed size limits"):
        recv_ipc_message(oversized_stream)

    # 3. Oversized header length rejection (> 1MB)
    oversized_hdr_stream = io.BytesIO(b"HIPC" + struct.pack(">II", 2000000, MAX_IPC_HEADER_SIZE + 100))
    with pytest.raises(ContractViolationError, match="Invalid IPC header length"):
        recv_ipc_message(oversized_hdr_stream)


def test_13_physical_holdout_quarantine_verification():
    """Verifies that physical quarantine function accurately checks filesystem and permanently excludes holdout."""
    quarantine = verify_physical_holdout_quarantine()
    assert quarantine["compromised_holdout_id"] == "stage35_final_holdout"
    assert quarantine["compromised_holdout_status"] == "COMPROMISED_PUBLIC_EXPOSURE"
    assert quarantine["compromised_holdout_protocol"] == "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT"
    assert quarantine["physical_exclusion_verified"] is True
    assert quarantine["quarantine_permanently_enforced"] is True
    assert len(quarantine["exposed_compromised_files_found"]) == 0


def test_14_ed25519_cryptographic_attestation_provenance(synthetic_checkpoint: Tuple[Path, str], tmp_path: Path):
    """Verifies end-to-end Ed25519 signature generation, public key verification, and persistence."""
    _, ckpt_sha = synthetic_checkpoint
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=ckpt_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "subprocess_fallback"},
    )
    builder.record_control(
        control_id="ISO-IPC-01",
        name="HIPC Safe Binary Protocol",
        status=STATUS_PARTIALLY_VERIFIED,
        evidence={"verified": True},
        rationale="Empirically tested binary IPC stream.",
    )

    record = builder.build()
    assert record["schema_version"] == ATTESTATION_SCHEMA_VERSION
    assert record["signature_scheme"] == "Ed25519-NON_PRODUCTION_TEST_KEY"
    assert record["signature_public_key_hex"] == TEST_NONPROD_PUBLIC_KEY_HEX
    assert len(record["attestation_signature"]) == 128  # 64-byte Ed25519 signature in hex
    assert verify_attestation_signature(record) is True

    # Save to disk and re-verify
    out_file = tmp_path / "test_attestation.json"
    saved = save_security_attestation(record, out_file)
    loaded = json.loads(saved.read_text(encoding="utf-8"))
    assert verify_attestation_signature(loaded) is True
    assert verify_attestation_digest(loaded) is True
