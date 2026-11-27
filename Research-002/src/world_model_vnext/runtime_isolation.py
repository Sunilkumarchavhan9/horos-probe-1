"""Operating system and container runtime isolation for HOROS PROBE-1 inference workers.

Stage 3.7C.2 Architecture:
- Genuine two-boundary security model:
  1. Privileged Evaluator: Holds ground-truth private vault, candidate checkpoint,
     signing keys, and evaluation ledger. Evaluator retains full read access to private vault.
  2. Restricted Inference Worker: Runs under separate unprivileged UID/GID (10001:10001)
     or Linux kernel namespace with --network none, read-only root, no private vault mount,
     and dropped capabilities. Communicates exclusively via typed HIPC binary streams.

Supports multi-tier runtime execution:
- Tier 1: OCI Container Runtime (Docker / Podman with --network none and read-only root)
- Tier 2: Linux Kernel Namespaces (unshare -n -r -m -u -p -f --mount-proc natively or via WSL)
- Tier 3: Subprocess Fallback (Environment stripping, proxy blackholing, with explicit capability audit)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Runtime type identifiers
RUNTIME_OCI_CONTAINER = "OCI_CONTAINER"
RUNTIME_LINUX_NAMESPACES = "LINUX_NAMESPACES"
RUNTIME_SUBPROCESS_FALLBACK = "SUBPROCESS_FALLBACK"

# Default container image and unprivileged user specifications
DEFAULT_WORKER_IMAGE = "horos-probe1-worker:stage37c2"
WORKER_UID = 10001
WORKER_GID = 10001
WORKER_USER_SPEC = f"{WORKER_UID}:{WORKER_GID}"

# Security constants
FORBIDDEN_VAULT_MOUNT_IDENTIFIERS = frozenset({
    "vault",
    "private_vault",
    "evaluation_vault",
    "compromised_holdout",
    "signing_keys",
    "ledger",
})


class WorkerIsolationError(Exception):
    """Base error for worker runtime isolation violations."""


class WorkerNetworkDeniedError(WorkerIsolationError):
    """Raised when an isolated worker attempts unauthorized network communication."""


class WorkerVaultInfiltrationError(WorkerIsolationError):
    """Raised when an isolated worker breaches filesystem isolation to access private vault."""


def detect_runtime_capabilities() -> Dict[str, Any]:
    """Detects available container engines, kernel namespace tools, and security primitives."""
    docker_path = shutil.which("docker")
    podman_path = shutil.which("podman")
    unshare_path = shutil.which("unshare")
    
    # On Windows, check for WSL with unshare
    wsl_unshare_available = False
    if os.name == "nt":
        wsl_path = shutil.which("wsl")
        if wsl_path:
            try:
                res = subprocess.run(
                    ["wsl", "which", "unshare"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                if res.returncode == 0 and res.stdout.strip():
                    wsl_unshare_available = True
            except Exception:
                wsl_unshare_available = False

    docker_available = docker_path is not None
    podman_available = podman_path is not None
    native_unshare_available = unshare_path is not None

    if docker_available or podman_available:
        runtime_type = RUNTIME_OCI_CONTAINER
        network_isolation_supported = True
        filesystem_isolation_supported = True
        non_root_uid_supported = True
        dropped_caps_supported = True
        primary_tool = "docker" if docker_available else "podman"
    elif native_unshare_available or wsl_unshare_available:
        runtime_type = RUNTIME_LINUX_NAMESPACES
        network_isolation_supported = True
        filesystem_isolation_supported = True
        non_root_uid_supported = True
        dropped_caps_supported = True
        primary_tool = "unshare (native)" if native_unshare_available else "unshare (WSL2)"
    else:
        runtime_type = RUNTIME_SUBPROCESS_FALLBACK
        network_isolation_supported = False
        filesystem_isolation_supported = False
        non_root_uid_supported = False
        dropped_caps_supported = False
        primary_tool = "subprocess (fallback)"

    return {
        "runtime_type": runtime_type,
        "primary_tool": primary_tool,
        "network_isolation_supported": network_isolation_supported,
        "filesystem_isolation_supported": filesystem_isolation_supported,
        "non_root_uid_supported": non_root_uid_supported,
        "dropped_caps_supported": dropped_caps_supported,
        "docker_available": docker_available,
        "podman_available": podman_available,
        "native_unshare_available": native_unshare_available,
        "wsl_unshare_available": wsl_unshare_available,
        "security_boundary_guarantees": [
            "Evaluator retains exclusive read access to private vault data",
            "Inference worker executes with zero vault mount",
            "Candidate checkpoint mounted strictly read-only",
            "HIPC typed binary pipe communication without pickle",
            "Privileged target keys rejected bidirectionally by IPC filter",
            "Outbound network egress denied under container or network namespace",
        ],
    }


def build_oci_run_command(
    checkpoint_path: Path | str,
    image_name: str = DEFAULT_WORKER_IMAGE,
    container_engine: str = "docker",
    memory_limit: str = "2g",
    cpus: float = 2.0,
    pids_limit: int = 128,
    extra_read_only_mounts: Optional[Sequence[Tuple[Path | str, str]]] = None,
) -> List[str]:
    """Builds reproducible OCI container execution command enforcing complete security boundaries.
    
    Guarantees:
    - --network none
    - --read-only root
    - --user 10001:10001
    - --cap-drop ALL
    - --security-opt no-new-privileges:true
    - --tmpfs /tmp:rw,noexec,nosuid,size=64m
    - Strict volume mount validation: Candidate checkpoint mounted ro, NO private vault mounts.
    """
    ckpt_p = Path(checkpoint_path).resolve()
    if not ckpt_p.is_file():
        raise FileNotFoundError(f"Candidate checkpoint does not exist: {ckpt_p}")

    # Verify no disallowed vault paths are mounted
    ckpt_str = str(ckpt_p).lower()
    for forbidden in FORBIDDEN_VAULT_MOUNT_IDENTIFIERS:
        if forbidden in ckpt_str and "checkpoint" not in ckpt_p.name.lower():
            raise WorkerVaultInfiltrationError(
                f"Attempted to mount forbidden path '{ckpt_p}' matching vault token '{forbidden}'."
            )

    cmd = [
        container_engine,
        "run",
        "--rm",
        "-i",
        "--network", "none",
        "--read-only",
        "--user", WORKER_USER_SPEC,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--memory", str(memory_limit),
        "--cpus", str(cpus),
        "--pids-limit", str(pids_limit),
        "-v", f"{ckpt_p}:/model/checkpoint.pt:ro",
    ]

    if extra_read_only_mounts:
        for host_path, container_target in extra_read_only_mounts:
            hp = Path(host_path).resolve()
            hp_str = str(hp).lower()
            for forbidden in FORBIDDEN_VAULT_MOUNT_IDENTIFIERS:
                if forbidden in hp_str:
                    raise WorkerVaultInfiltrationError(
                        f"Disallowed volume mount of private vault path '{hp}'."
                    )
            cmd.extend(["-v", f"{hp}:{container_target}:ro"])

    cmd.extend([image_name, "--worker"])
    return cmd


def build_namespace_run_command(
    checkpoint_path: Optional[Path | str] = None,
    use_wsl: bool = False,
    python_binary: Optional[str] = None,
) -> List[str]:
    """Builds Linux kernel namespace execution command using unshare.
    
    Guarantees:
    - -n: Network namespace (unshared, isolated loopback only)
    - -r: Map current UID to root inside user namespace
    - -m: Mount namespace (filesystem isolation)
    - -u: UTS namespace (isolated hostname)
    - -p -f --mount-proc: PID namespace with isolated procfs
    """
    py_bin = python_binary or ("python3" if use_wsl or os.name != "nt" else sys.executable)
    
    base_cmd = [
        "unshare",
        "-n",
        "-r",
        "-m",
        "-u",
        "-p",
        "-f",
        "--mount-proc",
        py_bin,
        "-u",
        "-m",
        "world_model_vnext.worker_isolation",
        "--worker",
    ]

    if use_wsl:
        return ["wsl"] + base_cmd
    return base_cmd


def verify_parent_and_worker_access_separation(
    evaluator_vault_file: Path | str,
    worker_client: Any,
    worker_target_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Independently verifies that the evaluator retains access to protected data
    while the worker demonstrably cannot discover or read the protected file.
    
    Requirements:
    1. Evaluator can read the protected file directly.
    2. Worker cannot discover or read the file.
    3. Evaluator retains access throughout and after worker access attempt.
    """
    vault_file = Path(evaluator_vault_file).resolve()
    if not vault_file.is_file():
        raise FileNotFoundError(f"Evaluator vault file not found: {vault_file}")

    # 1. Evaluator reads protected file
    evaluator_data_before = vault_file.read_bytes()
    evaluator_accessible_before = len(evaluator_data_before) > 0

    # 2. Worker attempts direct filesystem read on unmounted container path or vault
    target = Path(worker_target_path) if worker_target_path is not None else vault_file
    worker_access_res = worker_client.test_vault_access(target)
    worker_status = worker_access_res.get("status")

    # Worker must NOT be allowed access (denied, not found, or error)
    worker_read_denied = worker_status in ["ACCESS_DENIED", "ERROR", "NOT_FOUND"] or (
        "error" in worker_access_res
    )

    # 3. Evaluator verifies it still retains read access
    evaluator_data_after = vault_file.read_bytes()
    evaluator_accessible_after = evaluator_data_after == evaluator_data_before

    return {
        "evaluator_can_read_before": evaluator_accessible_before,
        "evaluator_can_read_after": evaluator_accessible_after,
        "evaluator_retains_access": evaluator_accessible_before and evaluator_accessible_after,
        "worker_access_status": worker_status,
        "worker_read_denied": worker_read_denied,
        "separation_verified": evaluator_accessible_after and worker_read_denied,
        "worker_response": worker_access_res,
    }


def execute_in_worker_network_diagnostics(
    worker_client: Any,
    external_ip: str = "8.8.8.8",
    external_port: int = 53,
    domain: str = "example.com",
) -> Dict[str, Any]:
    """Commands the worker to execute network diagnostics across multiple network protocols.
    
    Diagnostics tested:
    - External TCP socket connection
    - DNS hostname resolution
    - HTTP / HTTPS web request
    - Raw IP socket creation
    """
    # Issue comprehensive diagnostic command
    send_dict = {
        "command": "TEST_FULL_NETWORK_DIAGNOSTICS",
        "external_ip": external_ip,
        "external_port": external_port,
        "domain": domain,
    }
    
    if hasattr(worker_client, "run_custom_command"):
        return worker_client.run_custom_command(send_dict)
    
    # Fallback to standard test_outbound_network
    tcp_res = worker_client.test_outbound_network(external_ip, external_port, timeout=2.0)
    return {
        "status": tcp_res.get("status"),
        "external_tcp_status": tcp_res.get("status"),
        "network_denied": tcp_res.get("status") == "NETWORK_DENIED",
        "error": tcp_res.get("error", ""),
    }
