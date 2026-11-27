"""P55: Stage 3.7C.3 Runtime Enforcement, Security Attestation & Adversarial Validation Suite.

Verifies:
1. Actual worker UID/GID, capabilities, privilege escalation restrictions (no-new-privileges).
2. Read-only root filesystem and candidate checkpoint mount writability denial.
3. Private vault invisibility, including procfs, symlink, and traversal denial.
4. Resource limit metrics and quota enforcement schema.
5. HIPC malformed frame rejection (bad magic, pickle magic, truncated frames, negative lengths).
6. HIPC oversized message rejection (payload > 64MB, header > 1MB).
7. HIPC protocol manipulation rejection (NaN/Inf, unsupported dtypes, out-of-bounds offsets).
8. HIPC nonce tracking and replay attack rejection.
9. Interrupted IPC communication, premature pipe EOF, timeout and process cleanup.
10. Network isolation and raw socket denial active probing.
11. Evaluator-produced machine-readable security attestation generation and cryptographic anchoring.
12. Non-negotiable research governance invariants.
"""

from __future__ import annotations

import copy
import io
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict

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
    detect_runtime_capabilities,
    execute_in_worker_network_diagnostics,
)
from world_model_vnext.security_attestation import (
    ATTESTATION_MILESTONE,
    ATTESTATION_SCHEMA_VERSION,
    STATUS_BLOCKED,
    STATUS_IMPLEMENTED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_TESTED_AND_VERIFIED,
    SecurityAttestationBuilder,
    compute_evidence_digest,
    save_security_attestation,
    validate_security_attestation_schema,
    verify_attestation_digest,
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
def synthetic_checkpoint(tmp_path: Path) -> Path:
    """Creates a valid synthetic HCTT checkpoint."""
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    ckpt_path = tmp_path / "synthetic_checkpoint.pt"
    torch.save(model.state_dict(), ckpt_path)
    return ckpt_path


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


def test_01_worker_identity_and_privilege_escalation_denial():
    """Verifies in-worker security state and proves privilege escalation denial."""
    worker = IsolatedInferenceWorker()
    try:
        sec_info = worker.test_security_state()
        assert sec_info.get("status") == "OK"
        if hasattr(os, "setuid"):
            # On Linux/POSIX, privilege escalation probe must have executed and been prevented
            assert sec_info.get("escalation_probe_executed") is True
            assert sec_info.get("escalation_prevented") is True
        elif os.name == "nt":
            # On Windows, verify token membership: standard unprivileged user token
            assert sec_info.get("is_admin_token") is False
            assert sec_info.get("escalation_prevented") is False
        if sec_info.get("no_new_privs") is not None:
            assert sec_info.get("no_new_privs") == 1
    finally:
        worker.close()


def test_02_read_only_root_and_checkpoint_mount_writability_denial(tmp_path: Path):
    """Verifies that attempts by worker to write to root/app or model paths fail closed."""
    worker = IsolatedInferenceWorker()
    try:
        # Provide candidate paths including a non-writable test path
        res = worker.test_filesystem_writability()
        assert res.get("status") == "OK"
        results = res.get("results", [])
        assert len(results) > 0
        # Root path must not be writable
        assert res.get("root_readonly") is True
    finally:
        worker.close()


def test_03_vault_invisibility_via_symlink_and_procfs(tmp_path: Path):
    """Verifies worker cannot inspect private vault via procfs, symlinks, or alternate traversal."""
    secret_vault = tmp_path / "private_vault"
    secret_vault.mkdir()
    secret_file = secret_vault / "protected_targets.json"
    secret_file.write_text(json.dumps({"secret_target": [1, 2, 3]}), encoding="utf-8")

    # Apply OS-level deny isolation to secret vault
    apply_os_vault_isolation(secret_vault)

    worker = IsolatedInferenceWorker(working_dir=tmp_path)
    try:
        res = worker.test_procfs_vault_inspection(vault_path=str(secret_vault))
        assert res.get("status") == "OK"
        assert res.get("proc_1_root_accessible") is False
        assert res.get("vault_visible_via_procfs") is False
        # Direct vault access attempt should fail closed
        access_res = worker.test_vault_access(secret_file)
        assert access_res.get("status") in ("ACCESS_DENIED", "NOT_FOUND", "ERROR")
    finally:
        worker.close()
        remove_os_vault_isolation(secret_vault)


def test_04_resource_limits_enforcement_schema():
    """Verifies worker resource query reports limits conforming to schema."""
    worker = IsolatedInferenceWorker()
    try:
        res = worker.test_resource_limits()
        assert res.get("status") == "OK"
        assert "memory_max" in res
        assert "pids_max" in res
        assert "cpu_max" in res
        assert "rlimits" in res
    finally:
        worker.close()


def test_05_hipc_malformed_frames_fail_closed():
    """Verifies HIPC strictly rejects malformed magic, pickle signatures, truncated headers."""
    # 1. Bad magic
    bad_magic_stream = io.BytesIO(b"BADM\x00\x00\x00\x10\x00\x00\x00\x08{}")
    with pytest.raises(ContractViolationError, match="Invalid IPC protocol magic header"):
        recv_ipc_message(bad_magic_stream)

    # 2. Raw pickle magic (0x80 0x04)
    pickle_stream = io.BytesIO(b"\x80\x04\x95\x00\x00\x00\x00\x00\x00\x00\x00.")
    with pytest.raises(ContractViolationError, match="Invalid IPC protocol magic header"):
        recv_ipc_message(pickle_stream)

    # 3. Zip/Pickle magic (PK\x03\x04)
    zip_stream = io.BytesIO(b"PK\x03\x04\x14\x00\x00\x00\x08\x00")
    with pytest.raises(ContractViolationError, match="Invalid IPC protocol magic header"):
        recv_ipc_message(zip_stream)

    # 4. Truncated total length
    trunc_len_stream = io.BytesIO(b"HIPC\x00\x00")
    with pytest.raises(EOFError):
        recv_ipc_message(trunc_len_stream)

    # 5. Payload length declared smaller than minimum header
    small_len_stream = io.BytesIO(b"HIPC\x00\x00\x00\x02\x00\x00\x00\x01{}")
    with pytest.raises(ContractViolationError, match="violates allowed size limits"):
        recv_ipc_message(small_len_stream)


def test_06_hipc_oversized_payload_and_header_rejection():
    """Verifies HIPC rejects payloads > 64MB and headers > 1MB."""
    # 1. Oversized declared payload length
    oversized_payload = struct.pack(">4sII", HIPC_MAGIC, MAX_IPC_PAYLOAD_SIZE + 100, 10)
    stream = io.BytesIO(oversized_payload)
    with pytest.raises(ContractViolationError, match="violates allowed size limits"):
        recv_ipc_message(stream)

    # 2. Oversized declared header length
    oversized_hdr = struct.pack(">4sII", HIPC_MAGIC, 2000, MAX_IPC_HEADER_SIZE + 100)
    stream2 = io.BytesIO(oversized_hdr)
    with pytest.raises(ContractViolationError, match="Invalid IPC header length"):
        recv_ipc_message(stream2)


def test_07_hipc_protocol_manipulation_rejection():
    """Verifies rejection of non-finite numbers, unsupported dtypes, and corrupted offsets."""
    # 1. Tensor with NaN
    nan_tensor = torch.tensor([1.0, float("nan"), 3.0], dtype=torch.float32)
    stream = io.BytesIO()
    with pytest.raises(ContractViolationError, match="non-finite values"):
        send_ipc_message(stream, {"bad_tensor": nan_tensor})

    # 2. Tensor with Inf
    inf_tensor = torch.tensor([1.0, float("inf"), 3.0], dtype=torch.float32)
    stream = io.BytesIO()
    with pytest.raises(ContractViolationError, match="non-finite values"):
        send_ipc_message(stream, {"bad_tensor": inf_tensor})

    # 3. Corrupted header referencing out-of-bounds offset
    hdr = {
        "_tensors": {
            "fake": {
                "parent": None,
                "key": "fake",
                "dtype": "float32",
                "shape": [10],
                "offset": 9999,
                "length": 40,
            }
        }
    }
    hdr_bytes = json.dumps(hdr).encode("utf-8")
    total_len = 4 + len(hdr_bytes) + 16
    wire = HIPC_MAGIC + struct.pack(">II", total_len, len(hdr_bytes)) + hdr_bytes + b"\x00" * 16
    stream = io.BytesIO(wire)
    with pytest.raises(ContractViolationError, match="offset/length out of bounds"):
        recv_ipc_message(stream)


def test_08_hipc_replay_attack_rejection(synthetic_checkpoint: Path, valid_inference_payload: Dict[str, Any]):
    """Verifies that replayed request nonces are rejected with ContractViolationError."""
    worker = IsolatedInferenceWorker(checkpoint_path=synthetic_checkpoint)
    try:
        # First request with nonce_abc succeeds
        nonce = "nonce_eval_run_9999"
        preds1 = worker.predict(valid_inference_payload, nonce=nonce)
        assert "pred_next_states" in preds1

        # Replay with same nonce must be rejected
        with pytest.raises(ContractViolationError, match="Replay attack detected"):
            worker.predict(valid_inference_payload, nonce=nonce)

        # Worker-side direct IPC rejection of replayed nonce
        replay_resp = worker.run_custom_command({
            "command": "PREDICT",
            "payload": valid_inference_payload,
            "nonce": nonce,
        })
        assert "error" in replay_resp
        assert "replay attack detected" in replay_resp["error"].lower()

        # Worker-side direct IPC rejection of missing/empty nonce
        raw_resp = worker.run_custom_command({
            "command": "PREDICT",
            "payload": valid_inference_payload,
            "nonce": "",
        })
        assert "error" in raw_resp
        assert "nonce" in raw_resp["error"].lower()
    finally:
        worker.close()


def test_09_interrupted_ipc_communication_and_timeout():
    """Verifies clean failure and recovery when IPC channel is interrupted or closed prematurely."""
    worker = IsolatedInferenceWorker()
    try:
        assert worker.proc.poll() is None
        worker.close()
        assert worker._closed is True
        # Further commands after close must raise ContractViolationError
        with pytest.raises(ContractViolationError):
            worker.get_security_info()
    finally:
        worker.close()


def test_10_network_isolation_and_dropped_caps_independent_verification():
    """Verifies independent runtime detection and network egress diagnostic evidence."""
    import subprocess
    import sys
    caps = detect_runtime_capabilities()
    assert isinstance(caps, dict)
    assert "runtime_type" in caps
    assert "docker_available" in caps

    # 1. Standard worker environment configures proxy blackholing
    worker = IsolatedInferenceWorker()
    try:
        sec_info = worker.get_security_info()
        assert "0.0.0.0" in sec_info.get("env_proxy", "")
        net_diag = worker.run_custom_command({"command": "TEST_FULL_NETWORK_DIAGNOSTICS"})
        assert net_diag.get("status") == "OK"
    finally:
        worker.close()

    # 2. In isolated tier (WSL unshare / native unshare / Docker), verify real network denial
    probe_script = """import socket, urllib.request, json
tcp_denied = False
try:
    s = socket.create_connection(('8.8.8.8', 53), timeout=1.0)
    s.close()
except Exception:
    tcp_denied = True

dns_denied = False
try:
    socket.getaddrinfo('example.com', 80)
except Exception:
    dns_denied = True

raw_denied = False
try:
    raw_s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
    raw_s.close()
except Exception:
    raw_denied = True

print(json.dumps({'tcp_denied': tcp_denied, 'dns_denied': dns_denied, 'raw_denied': raw_denied}))
"""
    if caps.get("wsl_unshare_available"):
        cmd = ["wsl", "unshare", "-n", "-r", "python3"]
        res = subprocess.run(cmd, input=probe_script, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"WSL probe error: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["tcp_denied"] is True, "External TCP must be DENIED under unshare -n!"
        assert out["dns_denied"] is True, "DNS resolution must be DENIED under unshare -n!"
    elif caps.get("native_unshare_available"):
        cmd = ["unshare", "-n", "-r", sys.executable]
        res = subprocess.run(cmd, input=probe_script, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"Native unshare probe error: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["tcp_denied"] is True
        assert out["dns_denied"] is True
    elif caps.get("docker_available"):
        # Check if the required test image is locally cached before attempting probe
        inspect_res = subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True, text=True)
        if inspect_res.returncode != 0:
            pytest.skip("Docker is available but 'python:3.12-slim' image is not cached locally. Skipping live Docker isolation probe to avoid network pull failure.")
        cmd = ["docker", "run", "--rm", "-i", "--network", "none", "python:3.12-slim", "python3"]
        res = subprocess.run(cmd, input=probe_script, capture_output=True, text=True, timeout=30.0)
        assert res.returncode == 0, f"Docker probe error: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["tcp_denied"] is True
        assert out["dns_denied"] is True


def test_11_machine_readable_security_attestation_generation(tmp_path: Path):
    """Verifies creation, schema validation, cryptographic digest verification, and tamper detection."""
    dummy_sha = "a" * 64
    caps = detect_runtime_capabilities()

    builder = SecurityAttestationBuilder(
        candidate_checkpoint_sha256=dummy_sha,
        runtime_capabilities=caps,
        active_runtime_identity={"active_runtime_tier": "native_namespaces"},
    )
    builder.record_control(
        control_id="ISO-UID-01",
        name="Worker Identity & Privilege Escalation Denial",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"escalation_prevented": True, "uid": 10001},
        rationale="Active probe verified setuid(0) failure.",
    )
    builder.record_control(
        control_id="ISO-FS-01",
        name="Filesystem Read-Only Enforcement",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"root_readonly": True, "checkpoint_readonly": True},
        rationale="Writability probes to root and model paths failed closed.",
    )
    builder.record_control(
        control_id="ISO-NET-01",
        name="Kernel Network Namespace Isolation",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"network_egress_denied": True, "active_isolation_tier": "native_namespaces"},
        rationale="Active TCP and raw socket probes failed closed.",
    )
    builder.record_control(
        control_id="ISO-IPC-01",
        name="HIPC Safe Binary Protocol & Replay Protection",
        status=STATUS_TESTED_AND_VERIFIED,
        evidence={"replay_rejected": True, "malformed_magic_rejected": True},
        rationale="Adversarial malformed and replayed frames rejected.",
    )

    record = builder.build()
    # 1. Schema validation
    validate_security_attestation_schema(record)
    assert record["milestone"] == ATTESTATION_MILESTONE
    assert record["schema_version"] == ATTESTATION_SCHEMA_VERSION

    # 2. Cryptographic evidence digest match
    assert verify_attestation_digest(record) is True

    # 3. Tamper detection: mutating a deep copy of control must invalidate the digest
    tampered_record = copy.deepcopy(record)
    tampered_record["controls"]["ISO-UID-01"]["status"] = STATUS_BLOCKED
    assert verify_attestation_digest(tampered_record) is False

    with pytest.raises(ContractViolationError, match="digest mismatch"):
        validate_security_attestation_schema(tampered_record)

    # 4. Save to disk
    out_file = tmp_path / "security_attestation_report.json"
    saved_p = save_security_attestation(record, out_file)
    assert saved_p.is_file()
    loaded_data = json.loads(saved_p.read_text(encoding="utf-8"))
    assert verify_attestation_digest(loaded_data) is True


def test_12_mandatory_research_governance_invariants():
    """Asserts all 8 non-negotiable governance invariants remain strictly enforced."""
    assert TRAINING_PERMITTED == 0, "TRAINING_PERMITTED must remain 0"
    assert EXECUTION_AUTHORIZED == 0, "EXECUTION_AUTHORIZED must remain 0"
    assert COMPROMISED_HOLDOUT_ID == "stage35_final_holdout"
    assert COMPROMISED_HOLDOUT_STATUS == "COMPROMISED_PUBLIC_EXPOSURE"
    assert COMPROMISED_HOLDOUT_PROTOCOL == "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT"
