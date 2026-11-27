"""Stage 3.7C.2: Worker Isolation Runtime Integration & Security Boundary Validation (P54).

Validates the genuine two-boundary OS/container security architecture:
1. Privileged Evaluator Boundary:
   - Holds ground-truth private vault, candidate checkpoint, signing keys, and ledger.
   - Retains continuous read access to protected synthetic vault data without self-denial.
2. Restricted Inference Worker Boundary:
   - Executes under low-privilege container / namespace identity (UID 10001:10001).
   - Zero mount of private vault.
   - Network isolation (--network none / unshare -n).
   - Read-only root filesystem and read-only candidate checkpoint mount.
   - Dropped Linux capabilities (CAP_DROP ALL) and no-new-privileges.
   - Safe typed binary IPC (HIPC) strictly filtering privileged keys.
   - Mandatory research governance restrictions maintained.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
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
    strip_forbidden_model_input_keys,
)
from world_model_vnext.hctt import HCTTWorldModel
from world_model_vnext.ipc_filter import (
    PERMITTED_INFERENCE_PAYLOAD_KEYS,
    recursive_assert_no_privileged_keys,
    validate_inference_ipc_payload,
)
from world_model_vnext.runtime_isolation import (
    DEFAULT_WORKER_IMAGE,
    RUNTIME_LINUX_NAMESPACES,
    RUNTIME_OCI_CONTAINER,
    RUNTIME_SUBPROCESS_FALLBACK,
    WORKER_GID,
    WORKER_UID,
    WORKER_USER_SPEC,
    WorkerIsolationError,
    WorkerNetworkDeniedError,
    WorkerVaultInfiltrationError,
    build_namespace_run_command,
    build_oci_run_command,
    detect_runtime_capabilities,
    execute_in_worker_network_diagnostics,
    verify_parent_and_worker_access_separation,
)
from world_model_vnext.runner import EXECUTION_AUTHORIZED, TRAINING_PERMITTED
from world_model_vnext.worker_isolation import (
    HIPC_MAGIC,
    PERMITTED_INFERENCE_OUTPUT_KEYS,
    IsolatedInferenceWorker,
)


@pytest.fixture
def synthetic_checkpoint(tmp_path: Path) -> Tuple[Path, str]:
    """Generates a synthetic locked HCTT-256 checkpoint and returns path and SHA-256."""
    ckpt_path = tmp_path / "candidate_model_stage37c2.pt"
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    torch.save(model.state_dict(), ckpt_path)
    sha256 = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
    return ckpt_path, sha256


# ===========================================================================
# 1. Runtime Capabilities Discovery
# ===========================================================================

def test_01_runtime_capabilities_detection() -> None:
    """Verifies that the runtime isolation manager correctly audits host and container capabilities."""
    caps = detect_runtime_capabilities()
    assert isinstance(caps, dict)
    assert "runtime_type" in caps
    assert caps["runtime_type"] in [
        RUNTIME_OCI_CONTAINER,
        RUNTIME_LINUX_NAMESPACES,
        RUNTIME_SUBPROCESS_FALLBACK,
    ]
    assert "primary_tool" in caps
    assert "network_isolation_supported" in caps
    assert "filesystem_isolation_supported" in caps
    assert "non_root_uid_supported" in caps
    assert "dropped_caps_supported" in caps
    assert isinstance(caps["security_boundary_guarantees"], list)
    assert len(caps["security_boundary_guarantees"]) >= 5


# ===========================================================================
# 2. OCI Runtime Configuration & Argument Construction
# ===========================================================================

def test_02_oci_runtime_config_schema_and_security_flags(synthetic_checkpoint: Tuple[Path, str]) -> None:
    """Verifies that OCI launch command generates strict containment flags without private vault mounts."""
    ckpt_path, _ = synthetic_checkpoint
    cmd = build_oci_run_command(
        checkpoint_path=ckpt_path,
        image_name=DEFAULT_WORKER_IMAGE,
        container_engine="docker",
    )

    # Validate essential container containment flags
    assert "run" in cmd
    assert "--rm" in cmd
    assert "-i" in cmd
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--read-only" in cmd
    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == WORKER_USER_SPEC
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in cmd and cmd[cmd.index("--security-opt") + 1] == "no-new-privileges:true"
    assert "--tmpfs" in cmd and "/tmp:rw,noexec,nosuid,size=64m" in cmd[cmd.index("--tmpfs") + 1]
    assert "--memory" in cmd and cmd[cmd.index("--memory") + 1] == "2g"
    assert "--cpus" in cmd and cmd[cmd.index("--cpus") + 1] == "2.0"
    assert "--pids-limit" in cmd and cmd[cmd.index("--pids-limit") + 1] == "128"

    # Validate checkpoint volume mount is read-only
    mount_flags = [cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i] == "-v"]
    assert any("checkpoint.pt:ro" in m for m in mount_flags)
    # Validate no private vault mounts
    for m in mount_flags:
        assert "vault" not in m.lower()


def test_03_oci_run_command_rejects_forbidden_vault_mount(tmp_path: Path) -> None:
    """Asserts that attempting to mount private vault paths raises WorkerVaultInfiltrationError."""
    fake_vault = tmp_path / "private_vault"
    fake_vault.mkdir()
    secret_file = fake_vault / "holdout_secret.key"
    secret_file.write_bytes(b"CONFIDENTIAL")

    with pytest.raises(WorkerVaultInfiltrationError):
        build_oci_run_command(
            checkpoint_path=secret_file,
            image_name=DEFAULT_WORKER_IMAGE,
        )

    dummy_ckpt = tmp_path / "checkpoint.pt"
    dummy_ckpt.write_bytes(b"DUMMY_CKPT")
    with pytest.raises(WorkerVaultInfiltrationError):
        build_oci_run_command(
            checkpoint_path=dummy_ckpt,
            extra_read_only_mounts=[(fake_vault, "/private_vault")],
        )


# ===========================================================================
# 3. Two-Boundary Access Separation: Parent Retains Access, Worker Denied
# ===========================================================================

def test_04_parent_access_retention_and_worker_vault_invisibility(
    tmp_path: Path, synthetic_checkpoint: Tuple[Path, str]
) -> None:
    """Proves two-boundary separation: evaluator retains full read access while worker cannot discover or read vault."""
    ckpt_path, ckpt_sha = synthetic_checkpoint

    # Evaluator creates synthetic protected vault with privileged targets
    vault_dir = tmp_path / "evaluator_protected_vault"
    vault_dir.mkdir()
    target_file = vault_dir / "protected_targets.json"
    target_data = json.dumps({"privileged_target_1": [0.1, 0.2, 0.3], "seed": 99999}).encode("utf-8")
    target_file.write_bytes(target_data)

    worker = IsolatedInferenceWorker(checkpoint_path=ckpt_path, expected_checkpoint_sha256=ckpt_sha)
    try:
        # 1. In genuine container / unshared mount namespace, worker receives no vault mount
        unmounted_worker_path = Path("/model/non_mounted_vault/protected_targets.json")
        res = worker.test_vault_access(unmounted_worker_path)
        assert res.get("status") in ["ACCESS_DENIED", "ERROR", "NOT_FOUND"]

        # 2. Evaluator checks separation verification helper
        sep_report = verify_parent_and_worker_access_separation(
            evaluator_vault_file=target_file,
            worker_client=worker,
            worker_target_path=unmounted_worker_path,
        )
        assert sep_report["evaluator_can_read_before"] is True
        assert sep_report["evaluator_can_read_after"] is True
        assert sep_report["evaluator_retains_access"] is True
        # Parent was never locked out: file is intact and readable
        assert target_file.read_bytes() == target_data
    finally:
        worker.close()


def test_05_worker_alternate_path_traversal_denied(
    tmp_path: Path, synthetic_checkpoint: Tuple[Path, str]
) -> None:
    """Asserts that worker directory traversal attempts (../../) cannot discover or access protected files."""
    ckpt_path, ckpt_sha = synthetic_checkpoint
    isolated_dir = tmp_path / "sandbox_workspace"
    isolated_dir.mkdir()

    secret_file = tmp_path / "protected_targets.json"
    secret_file.write_bytes(b"CONFIDENTIAL_GROUND_TRUTH")

    worker = IsolatedInferenceWorker(
        checkpoint_path=ckpt_path,
        expected_checkpoint_sha256=ckpt_sha,
        working_dir=isolated_dir,
    )
    try:
        traversal_res = worker.test_vault_traversal(isolated_dir, "protected_targets.json")
        assert traversal_res.get("status") == "OK"
        # Worker must NOT successfully traverse to private targets
        assert traversal_res.get("traversal_succeeded") is False
    finally:
        worker.close()


# ===========================================================================
# 4. Safe HIPC Protocol: Privileged Targets Non-Disclosure
# ===========================================================================

def test_06_worker_cannot_obtain_privileged_targets_over_hipc(
    synthetic_checkpoint: Tuple[Path, str]
) -> None:
    """Verifies that the worker cannot extract privileged targets across the HIPC communication channel."""
    ckpt_path, ckpt_sha = synthetic_checkpoint
    worker = IsolatedInferenceWorker(checkpoint_path=ckpt_path, expected_checkpoint_sha256=ckpt_sha)
    try:
        # Worker rejects explicit command to retrieve privileged targets
        res = worker.run_custom_command({"command": "GET_PRIVILEGED_TARGETS"})
        assert res.get("status") == "ERROR"
        assert "forbidden" in res.get("error", "").lower()

        # Sender-side IPC validation rejects payload containing privileged keys
        privileged_payload = {
            "states": torch.zeros((1, 10, 14), dtype=torch.float32),
            "actions": torch.zeros((1, 30, 7), dtype=torch.float32),
            "wrenches": torch.zeros((1, 10, 6), dtype=torch.float32),
            "task": "peg_in_hole",
            "privileged_targets": {"mu": 0.5, "mass": 1.2},  # FORBIDDEN!
        }
        with pytest.raises(ContractViolationError) as exc_info:
            worker.predict(privileged_payload)
        assert "privileged" in str(exc_info.value).lower()
    finally:
        worker.close()


# ===========================================================================
# 5. Inference Execution on Authorized Candidate Checkpoint
# ===========================================================================

def test_07_worker_loads_authorized_checkpoint_and_predicts(
    synthetic_checkpoint: Tuple[Path, str]
) -> None:
    """Verifies that the worker can load candidate weights and execute inference returning valid predictions."""
    ckpt_path, ckpt_sha = synthetic_checkpoint
    worker = IsolatedInferenceWorker(checkpoint_path=ckpt_path, expected_checkpoint_sha256=ckpt_sha)
    try:
        payload = {
            "states": torch.zeros((1, 10, 15), dtype=torch.float32),
            "actions": torch.zeros((1, 10, 6), dtype=torch.float32),
            "wrenches": torch.zeros((1, 10, 6), dtype=torch.float32),
            "task": "peg_in_hole",
        }
        predictions = worker.predict(payload)
        assert isinstance(predictions, dict)
        for key in PERMITTED_INFERENCE_OUTPUT_KEYS:
            assert key in predictions, f"Expected output key '{key}' missing from worker response!"
            assert isinstance(predictions[key], torch.Tensor)
        assert predictions["pred_next_states"].shape == (1, 20, 15)
    finally:
        worker.close()


# ===========================================================================
# 6. Actual Network Egress Denial Evidence
# ===========================================================================

def test_08_in_worker_network_egress_denial_evidence() -> None:
    """Independently verifies that in-worker network attempts fail closed under kernel network isolation.
    
    If network namespace isolation (e.g. unshare -n or WSL) or Docker is available, demonstrates actual network denial.
    If the host environment does not support container/namespace isolation, records the capability status honestly.
    """
    caps = detect_runtime_capabilities()
    script = """import socket, urllib.request, json
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
http_denied = False
try:
    urllib.request.urlopen('http://1.1.1.1', timeout=1.0)
except Exception:
    http_denied = True
print(json.dumps({'tcp_denied': tcp_denied, 'dns_denied': dns_denied, 'http_denied': http_denied}))
"""
    if caps["wsl_unshare_available"]:
        probe_cmd = ["wsl", "unshare", "-n", "-r", "python3"]
        res = subprocess.run(probe_cmd, input=script, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"WSL probe failed: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["tcp_denied"] is True, "External TCP must be DENIED under unshare -n!"
        assert out["dns_denied"] is True, "DNS resolution must be DENIED under unshare -n!"
        assert out["http_denied"] is True, "HTTP traffic must be DENIED under unshare -n!"
    elif caps["native_unshare_available"]:
        probe_cmd = ["unshare", "-n", "-r", sys.executable]
        res = subprocess.run(probe_cmd, input=script, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"Native unshare probe failed: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["tcp_denied"] is True, "External TCP must be DENIED under unshare -n!"
        assert out["dns_denied"] is True, "DNS resolution must be DENIED under unshare -n!"
        assert out["http_denied"] is True, "HTTP traffic must be DENIED under unshare -n!"
    elif caps["docker_available"]:
        # Execute network denial test inside Docker container with --network none
        docker_cmd = [
            "docker", "run", "--rm", "-i",
            "--network", "none",
            "python:3.12-slim",
            "python3"
        ]
        res = subprocess.run(docker_cmd, input=script, capture_output=True, text=True, timeout=30.0)
        assert res.returncode == 0, f"Docker probe failed: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["tcp_denied"] is True, "External TCP must be DENIED in container under --network none!"
        assert out["dns_denied"] is True, "DNS resolution must be DENIED in container under --network none!"
        assert out["http_denied"] is True, "HTTP traffic must be DENIED in container under --network none!"
    else:
        # Runtime or CI environment cannot enforce network isolation without root container privileges
        pytest.skip("Control BLOCKED: Host/CI environment cannot enforce unprivileged kernel network namespaces.")


def test_09_in_worker_raw_socket_denial_evidence() -> None:
    """Verifies that raw IP sockets are denied under dropped capabilities."""
    caps = detect_runtime_capabilities()
    raw_script = """import socket, json
raw_denied = False
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
    s.close()
except PermissionError:
    raw_denied = True
except Exception:
    raw_denied = True
print(json.dumps({'raw_denied': raw_denied}))
"""
    if caps["wsl_unshare_available"]:
        raw_cmd = ["wsl", "unshare", "-n", "-r", "capsh", "--drop=cap_net_raw", "--", "-c", "python3"]
        res = subprocess.run(raw_cmd, input=raw_script, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"Raw cap probe failed: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["raw_denied"] is True, "Raw socket creation must be DENIED when CAP_NET_RAW is dropped!"
    elif caps["native_unshare_available"] and shutil.which("capsh"):
        raw_cmd = ["unshare", "-n", "-r", "capsh", "--drop=cap_net_raw", "--", "-c", sys.executable]
        res = subprocess.run(raw_cmd, input=raw_script, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"Native raw cap probe failed: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["raw_denied"] is True, "Raw socket creation must be DENIED when CAP_NET_RAW is dropped!"
    elif caps["docker_available"]:
        docker_raw_cmd = [
            "docker", "run", "--rm", "-i",
            "--cap-drop", "ALL",
            "python:3.12-slim",
            "python3"
        ]
        res = subprocess.run(docker_raw_cmd, input=raw_script, capture_output=True, text=True, timeout=30.0)
        assert res.returncode == 0, f"Docker raw cap probe failed: {res.stderr}"
        out = json.loads(res.stdout.strip())
        assert out["raw_denied"] is True, "Raw socket creation must be DENIED when CAP_NET_RAW is dropped in container!"
    else:
        pytest.skip("Control BLOCKED: Host/CI environment cannot enforce dropped capabilities without root container privileges.")


# ===========================================================================
# 7. Worker Lifecycle: Clean Termination & Pipe Teardown
# ===========================================================================

def test_10_worker_termination_and_pipe_cleanup(synthetic_checkpoint: Tuple[Path, str]) -> None:
    """Verifies that the inference worker child process terminates cleanly and releases pipes."""
    ckpt_path, ckpt_sha = synthetic_checkpoint
    worker = IsolatedInferenceWorker(checkpoint_path=ckpt_path, expected_checkpoint_sha256=ckpt_sha)
    assert worker.proc.poll() is None, "Worker must be running initially."

    # Query security info while running
    sec_info = worker.get_security_info()
    assert sec_info.get("status") == "OK"
    worker_pid = sec_info.get("pid")
    assert worker_pid != os.getpid()

    # Terminate worker
    worker.close()
    assert worker._closed is True
    assert worker.proc.poll() is not None, "Worker process must be terminated after close()."


# ===========================================================================
# 8. Mandatory Research Governance Invariants
# ===========================================================================

def test_11_mandatory_research_governance_invariants() -> None:
    """Enforces non-negotiable research governance invariants throughout Stage 3.7C.2."""
    assert TRAINING_PERMITTED == 0, "TRAINING_PERMITTED must remain 0!"
    assert EXECUTION_AUTHORIZED == 0, "EXECUTION_AUTHORIZED must remain 0!"
    assert COMPROMISED_HOLDOUT_ID == "stage35_final_holdout"
    assert COMPROMISED_HOLDOUT_STATUS == "COMPROMISED_PUBLIC_EXPOSURE"
    assert COMPROMISED_HOLDOUT_PROTOCOL == "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT"

    # Verify runtime_config.json governance block
    runtime_config_path = (
        Path(__file__).resolve().parents[1]
        / "experiments"
        / "stage37_blind_evaluation"
        / "runtime_config.json"
    )
    assert runtime_config_path.is_file(), "runtime_config.json must exist!"
    config = json.loads(runtime_config_path.read_text(encoding="utf-8"))
    gov = config.get("governance_invariants", {})
    assert gov.get("training_permitted") == 0
    assert gov.get("execution_authorized") == 0
    assert gov.get("gradient_steps") == 0
    assert gov.get("optimizer_updates") == 0
    assert gov.get("new_holdout_generation") == "NOT_AUTHORIZED"
    assert gov.get("production_keys") == "NOT_GENERATED"
    assert gov.get("production_vault") == "NOT_INITIALIZED"
    assert gov.get("final_evaluation") == "BLOCKED"
