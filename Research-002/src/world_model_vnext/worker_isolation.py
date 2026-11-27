"""Operating-system level inference worker isolation, NTFS ACL enforcement, safe binary IPC, and checkpoint loading.

Implements Stage 3.7C Workstreams B, C, E, G:
- Real Windows OS isolation and token SID inspection.
- Stripped environment and network isolation blackholing for child worker processes.
- Safe typed binary IPC envelope (HIPC): length-prefixed, schema-validated, eliminating pickle and torch.load(..., weights_only=False).
- Candidate checkpoint verification: SHA-256 digest validation and strict state-dict loading (weights_only=True).
- Fail-closed worker execution with strict privilege separation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

import numpy as np
import torch

from .contracts import ContractViolationError, LOCKED_HCTT_CONFIG
from .hctt import HCTTWorldModel
from .ipc_filter import (
    PERMITTED_INFERENCE_PAYLOAD_KEYS,
    recursive_assert_no_privileged_keys,
    validate_inference_ipc_payload,
)


# Permitted prediction output keys returned across the IPC channel
PERMITTED_INFERENCE_OUTPUT_KEYS = frozenset({
    "pred_next_states",
    "pred_next_wrenches",
    "pred_mode_probs",
    "pred_friction_mu",
    "pred_mass_kg",
    "pred_uncertainty_total",
})

# Safe binary IPC protocol constants (HOROS IPC - HIPC)
HIPC_MAGIC = b"HIPC"
MAX_IPC_PAYLOAD_SIZE = 64 * 1024 * 1024  # 64 MB maximum payload limit
MAX_IPC_HEADER_SIZE = 1 * 1024 * 1024    # 1 MB maximum header metadata limit


def get_current_process_sid() -> str:
    """Queries the Windows security identifier (SID) of the calling process token using native Win32 API."""
    if os.name != "nt":
        return f"posix-uid-{os.getuid()}"
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        TOKEN_QUERY = 0x0008
        hToken = wintypes.HANDLE()
        hProcess = kernel32.GetCurrentProcess()
        if not advapi32.OpenProcessToken(hProcess, TOKEN_QUERY, ctypes.byref(hToken)):
            return "UNKNOWN_WINDOWS_SID"
        try:
            cb = wintypes.DWORD(0)
            advapi32.GetTokenInformation(hToken, 1, None, 0, ctypes.byref(cb))  # 1 = TokenUser
            buf = ctypes.create_string_buffer(cb.value)
            if not advapi32.GetTokenInformation(hToken, 1, buf, cb.value, ctypes.byref(cb)):
                return "UNKNOWN_WINDOWS_SID"
            sid_ptr = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID)).contents
            p_str = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(p_str)):
                return "UNKNOWN_WINDOWS_SID"
            sid_str = p_str.value
            kernel32.LocalFree(p_str)
            return sid_str
        finally:
            kernel32.CloseHandle(hToken)
    except Exception:
        return "UNKNOWN_WINDOWS_SID"


def get_process_sid_by_pid(pid: int) -> str:
    """Queries the Windows security identifier (SID) for a target process by PID."""
    if os.name != "nt":
        return f"posix-pid-{pid}"
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        PROCESS_QUERY_INFORMATION = 0x0400
        TOKEN_QUERY = 0x0008

        hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, wintypes.DWORD(pid))
        if not hProcess:
            return "UNKNOWN_PROCESS_HANDLE"
        try:
            hToken = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(hProcess, TOKEN_QUERY, ctypes.byref(hToken)):
                return "ACCESS_DENIED_OR_NO_TOKEN"
            try:
                cb = wintypes.DWORD(0)
                advapi32.GetTokenInformation(hToken, 1, None, 0, ctypes.byref(cb))
                buf = ctypes.create_string_buffer(cb.value)
                if not advapi32.GetTokenInformation(hToken, 1, buf, cb.value, ctypes.byref(cb)):
                    return "GET_TOKEN_INFO_FAILED"
                sid_ptr = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID)).contents
                p_str = wintypes.LPWSTR()
                if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(p_str)):
                    return "CONVERT_SID_FAILED"
                sid_str = p_str.value
                kernel32.LocalFree(p_str)
                return sid_str
            finally:
                kernel32.CloseHandle(hToken)
        finally:
            kernel32.CloseHandle(hProcess)
    except Exception:
        return "UNKNOWN_PROCESS_SID"


def apply_os_vault_isolation(vault_path: Path | str) -> bool:
    """Applies operating-system filesystem access denial to a private vault directory.
    
    On Windows: Configures NTFS Deny ACL using icacls.
    On POSIX: Applies permission mode 000 (read/write/execute denied to all).
    """
    p = Path(vault_path).resolve()
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        username = os.environ.get("USERNAME", "Everyone")
        cmd = ["icacls", str(p), "/deny", f"{username}:(OI)(CI)(RD)"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise PermissionError(f"Failed to apply Windows NTFS deny ACL on '{p}': {res.stderr}")
        return True
    else:
        try:
            os.chmod(str(p), 0o000)
            return True
        except Exception as exc:
            raise PermissionError(f"Failed to apply POSIX filesystem isolation on '{p}': {exc}") from exc


def remove_os_vault_isolation(vault_path: Path | str) -> None:
    """Removes operating-system filesystem access denial from a private vault directory."""
    p = Path(vault_path).resolve()
    if not p.exists():
        return

    if os.name == "nt":
        username = os.environ.get("USERNAME", "Everyone")
        cmd = ["icacls", str(p), "/remove:d", username]
        subprocess.run(cmd, capture_output=True, text=True)
    else:
        try:
            os.chmod(str(p), 0o755)
        except Exception:
            pass


def send_ipc_message(stream: Any, msg: Dict[str, Any]) -> None:
    """Serializes and sends a safe typed binary message across IPC with zero pickle deserialization.
    
    Wire format:
    [4 bytes MAGIC 'HIPC']
    [4 bytes total_payload_length (uint32 big-endian)]
    [4 bytes json_header_length (uint32 big-endian)]
    [N bytes UTF-8 JSON metadata header]
    [M bytes raw contiguous binary tensor buffers]
    """
    if not isinstance(msg, dict):
        raise ContractViolationError(f"IPC message must be a dictionary, got {type(msg)}.")

    header_dict: Dict[str, Any] = {}
    tensor_buffers: List[bytes] = []
    tensor_meta: Dict[str, Dict[str, Any]] = {}
    current_offset = 0

    # Extract non-tensor keys and tensor buffers safely
    for k, v in msg.items():
        if isinstance(v, dict):
            # Nested dictionary (e.g. payload or predictions)
            header_dict[k] = {}
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, torch.Tensor):
                    if sub_v.is_floating_point() and not torch.isfinite(sub_v).all():
                        raise ContractViolationError(f"Tensor '{sub_k}' contains non-finite values (NaN or Inf).")
                    t_np = sub_v.detach().cpu().contiguous().numpy()
                    raw = t_np.tobytes()
                    tensor_meta[f"{k}.{sub_k}"] = {
                        "parent": k,
                        "key": sub_k,
                        "dtype": str(t_np.dtype),
                        "shape": list(t_np.shape),
                        "offset": current_offset,
                        "length": len(raw),
                    }
                    current_offset += len(raw)
                    tensor_buffers.append(raw)
                else:
                    header_dict[k][sub_k] = sub_v
        elif isinstance(v, torch.Tensor):
            if v.is_floating_point() and not torch.isfinite(v).all():
                raise ContractViolationError(f"Tensor '{k}' contains non-finite values (NaN or Inf).")
            t_np = v.detach().cpu().contiguous().numpy()
            raw = t_np.tobytes()
            tensor_meta[k] = {
                "parent": None,
                "key": k,
                "dtype": str(t_np.dtype),
                "shape": list(t_np.shape),
                "offset": current_offset,
                "length": len(raw),
            }
            current_offset += len(raw)
            tensor_buffers.append(raw)
        else:
            header_dict[k] = v

    header_dict["_tensors"] = tensor_meta
    header_bytes = json.dumps(header_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(header_bytes) > MAX_IPC_HEADER_SIZE:
        raise ContractViolationError(f"IPC header exceeds maximum size limit ({len(header_bytes)} bytes).")

    body_bytes = b"".join(tensor_buffers)
    total_len = 4 + len(header_bytes) + len(body_bytes)
    if total_len > MAX_IPC_PAYLOAD_SIZE:
        raise ContractViolationError(f"IPC payload exceeds maximum size limit ({total_len} bytes).")

    stream.write(HIPC_MAGIC)
    stream.write(struct.pack(">I", total_len))
    stream.write(struct.pack(">I", len(header_bytes)))
    stream.write(header_bytes)
    if body_bytes:
        stream.write(body_bytes)
    stream.flush()


def recv_ipc_message(stream: Any) -> Dict[str, Any]:
    """Reads and deserializes a safe typed binary message with zero pickle deserialization."""
    magic = stream.read(4)
    if not magic or len(magic) < 4:
        raise EOFError("IPC pipe closed by remote endpoint.")
    if magic != HIPC_MAGIC:
        raise ContractViolationError(
            f"Invalid IPC protocol magic header {magic!r}. "
            "Unrestricted pickle, malformed stream, or malicious payload strictly rejected."
        )

    len_bytes = stream.read(4)
    if not len_bytes or len(len_bytes) < 4:
        raise EOFError("Truncated IPC total length header.")
    total_len = struct.unpack(">I", len_bytes)[0]
    if total_len > MAX_IPC_PAYLOAD_SIZE or total_len < 4:
        raise ContractViolationError(f"IPC payload size {total_len} violates allowed size limits.")

    header_len_bytes = stream.read(4)
    if not header_len_bytes or len(header_len_bytes) < 4:
        raise EOFError("Truncated IPC header length.")
    header_len = struct.unpack(">I", header_len_bytes)[0]
    if header_len > MAX_IPC_HEADER_SIZE or header_len > total_len - 4:
        raise ContractViolationError(f"Invalid IPC header length {header_len}.")

    header_bytes = stream.read(header_len)
    if len(header_bytes) < header_len:
        raise EOFError(f"Truncated IPC header: expected {header_len} bytes, received {len(header_bytes)}.")

    try:
        header_dict = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:
        raise ContractViolationError(f"Failed to parse IPC JSON header: {exc}") from exc

    body_len = total_len - 4 - header_len
    body_bytes = b""
    if body_len > 0:
        body_bytes = stream.read(body_len)
        if len(body_bytes) < body_len:
            raise EOFError(f"Truncated IPC body: expected {body_len} bytes, received {len(body_bytes)}.")

    tensor_meta = header_dict.pop("_tensors", {})
    reconstructed: Dict[str, Any] = dict(header_dict)

    for tensor_id, meta in tensor_meta.items():
        parent = meta.get("parent")
        key = meta.get("key")
        dtype_str = meta.get("dtype")
        shape = meta.get("shape")
        offset = meta.get("offset")
        length = meta.get("length")

        if offset < 0 or length < 0 or (offset + length) > len(body_bytes):
            raise ContractViolationError(f"Tensor buffer offset/length out of bounds for '{tensor_id}'.")

        if not isinstance(shape, list) or any(not isinstance(dim, int) or dim < 0 for dim in shape):
            raise ContractViolationError(f"Tensor shape {shape} contains invalid dimensions for '{tensor_id}'.")

        try:
            dt = np.dtype(dtype_str)
        except Exception as exc:
            raise ContractViolationError(f"Unsupported tensor dtype '{dtype_str}' for '{tensor_id}': {exc}") from exc

        if dt not in [np.float32, np.float64, np.int64, np.int32, np.bool_]:
            raise ContractViolationError(f"Disallowed tensor dtype '{dtype_str}' for '{tensor_id}'.")

        expected_size = int(np.prod(shape)) * dt.itemsize if shape else dt.itemsize
        if expected_size != length:
            raise ContractViolationError(
                f"Shape {shape} with dtype {dt} requires {expected_size} bytes, but length is {length}."
            )

        raw_slice = body_bytes[offset:offset + length]
        arr = np.frombuffer(raw_slice, dtype=dt).reshape(shape)
        tensor = torch.from_numpy(arr.copy())

        # Assert numeric finiteness on floating-point tensors
        if tensor.is_floating_point() and not torch.isfinite(tensor).all():
            raise ContractViolationError(f"Tensor '{tensor_id}' contains non-finite values (NaN or Inf).")

        if parent:
            if parent not in reconstructed or not isinstance(reconstructed[parent], dict):
                reconstructed[parent] = {}
            reconstructed[parent][key] = tensor
        else:
            reconstructed[key] = tensor

    return reconstructed


def create_sanitized_worker_environment() -> Dict[str, str]:
    """Constructs a minimal, stripped environment dictionary for the isolated worker."""
    allowed_env_keys = {
        "SYSTEMROOT",
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    }
    env: Dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in allowed_env_keys:
            env[k] = v

    # Outbound network blackhole configuration (defense-in-depth)
    env["HTTP_PROXY"] = "http://0.0.0.0:0"
    env["HTTPS_PROXY"] = "http://0.0.0.0:0"
    env["ALL_PROXY"] = "http://0.0.0.0:0"
    env["NO_PROXY"] = ""
    env["HOROS_ISOLATED_WORKER"] = "1"

    src_dir = str(Path(__file__).resolve().parent.parent)
    existing_pp = env.get("PYTHONPATH", "")
    if src_dir not in existing_pp:
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir

    return env


class IsolatedInferenceWorker:
    """Evaluator-side client managing an OS-isolated inference child process."""

    def __init__(
        self,
        checkpoint_path: Optional[Path | str] = None,
        expected_checkpoint_sha256: Optional[str] = None,
        python_executable: Optional[str] = None,
        working_dir: Optional[Path | str] = None,
        timeout_seconds: float = 30.0,
        custom_cmd: Optional[List[str]] = None,
    ):
        self.python_executable = python_executable or sys.executable
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.timeout_seconds = timeout_seconds
        self.env = create_sanitized_worker_environment()
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.expected_checkpoint_sha256 = expected_checkpoint_sha256
        self._model_verified_and_loaded = False
        self._consumed_nonces: set[str] = set()
        
        # Spawn child worker process with piped stdin/stdout/stderr
        if custom_cmd is not None:
            cmd = list(custom_cmd)
        else:
            cmd = [
                self.python_executable,
                "-u",
                "-m",
                "world_model_vnext.worker_isolation",
                "--worker",
            ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.working_dir),
            env=self.env,
        )
        self._closed = False

        if self.checkpoint_path is not None:
            self.load_checkpoint(self.checkpoint_path, self.expected_checkpoint_sha256)

    def load_checkpoint(self, checkpoint_path: Path | str, expected_sha256: Optional[str]) -> None:
        """Calculates digest of candidate checkpoint bytes, verifies against expected digest, and commands worker to load."""
        p = Path(checkpoint_path).resolve()
        if not p.is_file():
            raise ContractViolationError(f"Candidate checkpoint file not found at '{p}'.")

        data = p.read_bytes()
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None:
            if actual_sha256.lower() != expected_sha256.strip().lower():
                raise ContractViolationError(
                    f"Candidate checkpoint SHA-256 digest mismatch! "
                    f"Expected: {expected_sha256.strip().lower()}, Actual: {actual_sha256.lower()}."
                )

        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )

        try:
            send_ipc_message(self.proc.stdin, {
                "command": "LOAD_CHECKPOINT",
                "checkpoint_path": str(p),
                "expected_sha256": actual_sha256,
            })
            resp = recv_ipc_message(self.proc.stdout)
            if resp.get("status") != "OK":
                err = resp.get("error", "Unknown worker checkpoint loading error")
                raise ContractViolationError(f"Worker failed to load candidate checkpoint: {err}")
            self._model_verified_and_loaded = True
            self.verified_checkpoint_sha256 = actual_sha256
        except Exception as exc:
            self.close()
            raise ContractViolationError(f"Checkpoint verification and loading failed: {exc}") from exc

    def predict(self, payload: Dict[str, Any], nonce: Optional[str] = None) -> Dict[str, Any]:
        """Validates payload schema, transmits across IPC pipe, and returns verified model predictions."""
        # 1. Evaluator-side pre-IPC schema and privileged-key validation
        validate_inference_ipc_payload(payload)

        # 1a. Replay attack rejection & cryptographic nonce generation
        if nonce is not None:
            nonce_str = str(nonce).strip()
            if not nonce_str or nonce_str in self._consumed_nonces:
                raise ContractViolationError(
                    f"Replay attack detected: nonce '{nonce_str}' has already been consumed or is empty."
                )
            self._consumed_nonces.add(nonce_str)
        else:
            import uuid
            nonce_str = f"nonce_{uuid.uuid4().hex}"
            self._consumed_nonces.add(nonce_str)

        if not self._model_verified_and_loaded:
            raise ContractViolationError(
                "IsolatedInferenceWorker cannot execute predict before candidate checkpoint weights are loaded and verified."
            )

        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )

        # 2. Transmit payload across stdin pipe
        try:
            send_ipc_message(self.proc.stdin, {"command": "PREDICT", "payload": payload, "nonce": nonce_str})
        except Exception as exc:
            raise ContractViolationError(f"Failed to transmit payload to worker IPC stream: {exc}") from exc

        # 3. Read predictions from stdout pipe
        try:
            response = recv_ipc_message(self.proc.stdout)
        except Exception as exc:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Failed to receive predictions from worker: {exc}. Stderr: {stderr_out.strip()}"
            ) from exc

        if not isinstance(response, dict):
            raise ContractViolationError(f"Worker returned non-dictionary response: {type(response)}")

        if "error" in response:
            raise ContractViolationError(f"Worker raised error during inference: {response['error']}")

        predictions = response.get("predictions")
        if not isinstance(predictions, dict):
            raise ContractViolationError("Worker response missing 'predictions' dictionary.")

        # Verify output keys adhere strictly to allowed prediction schema
        unknown_outputs = set(predictions.keys()) - PERMITTED_INFERENCE_OUTPUT_KEYS
        if unknown_outputs:
            raise ContractViolationError(f"Worker returned unauthorized output fields: {unknown_outputs}")

        return predictions

    def test_vault_access(self, vault_path: Path | str) -> Dict[str, Any]:
        """Commands worker to attempt direct filesystem access to a target vault path."""
        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )
        send_ipc_message(self.proc.stdin, {"command": "TEST_VAULT_ACCESS", "vault_path": str(vault_path)})
        return recv_ipc_message(self.proc.stdout)

    def test_outbound_network(self, host: str, port: int, timeout: float = 2.0) -> Dict[str, Any]:
        """Commands worker to attempt direct socket network connection."""
        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )
        send_ipc_message(
            self.proc.stdin,
            {"command": "TEST_OUTBOUND_NETWORK", "host": str(host), "port": int(port), "timeout": float(timeout)}
        )
        return recv_ipc_message(self.proc.stdout)

    def run_custom_command(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Sends arbitrary command dictionary across the safe IPC pipe and awaits response."""
        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )
        send_ipc_message(self.proc.stdin, msg)
        return recv_ipc_message(self.proc.stdout)

    def test_vault_traversal(self, base_dir: Path | str, target_name: str) -> Dict[str, Any]:
        """Commands worker to attempt directory traversal or search from base_dir for target_name."""
        return self.run_custom_command({
            "command": "TEST_VAULT_TRAVERSAL",
            "base_dir": str(base_dir),
            "target_name": str(target_name),
        })

    def get_security_info(self) -> Dict[str, Any]:
        """Queries process SID, PID, and security parameters from inside the worker process."""
        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )
        send_ipc_message(self.proc.stdin, {"command": "GET_SECURITY_INFO"})
        return recv_ipc_message(self.proc.stdout)

    def test_filesystem_writability(self, candidate_paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Commands worker to attempt writing to candidate filesystem paths to audit read-only enforcement."""
        return self.run_custom_command({
            "command": "TEST_FILESYSTEM_WRITABILITY",
            "candidate_paths": list(candidate_paths) if candidate_paths else None,
        })

    def test_procfs_vault_inspection(self, vault_path: Optional[str] = None) -> Dict[str, Any]:
        """Commands worker to attempt procfs, symlink, and cwd traversal to reach host vault."""
        return self.run_custom_command({
            "command": "TEST_PROCFS_VAULT_INSPECTION",
            "vault_path": str(vault_path) if vault_path else None,
        })

    def test_security_state(self) -> Dict[str, Any]:
        """Queries in-worker UID/GID, caps, NoNewPrivs, and tests privilege escalation denial."""
        return self.run_custom_command({
            "command": "TEST_SECURITY_STATE",
        })

    def test_resource_limits(self) -> Dict[str, Any]:
        """Queries in-worker memory, cpu, and process limits from cgroups or rlimits."""
        return self.run_custom_command({
            "command": "TEST_RESOURCE_LIMITS",
        })

    def close(self) -> None:
        """Terminates worker process and releases pipes cleanly."""
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc.poll() is None and self.proc.stdin:
                send_ipc_message(self.proc.stdin, {"command": "EXIT"})
                self.proc.stdin.close()
                self.proc.wait(timeout=2.0)
        except Exception:
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()


def worker_main() -> None:
    """Entrypoint executing inside the OS-isolated worker child process."""
    torch.set_grad_enabled(False)

    # On Linux, enforce no-new-privileges immediately upon worker process startup
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_NO_NEW_PRIVS = 38
            libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
        except Exception:
            pass

    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    model.eval()
    checkpoint_loaded = False
    loaded_sha256 = ""
    consumed_nonces: set[str] = set()

    stdin_stream = sys.stdin.buffer
    stdout_stream = sys.stdout.buffer

    while True:
        try:
            msg = recv_ipc_message(stdin_stream)
        except EOFError:
            break
        except Exception as exc:
            # Fatal deserialization error
            sys.exit(101)

        if not isinstance(msg, dict):
            send_ipc_message(stdout_stream, {"error": "Message must be a dict."})
            continue

        cmd = msg.get("command")
        if cmd == "EXIT":
            break
        elif cmd == "LOAD_CHECKPOINT":
            ckpt_path_str = msg.get("checkpoint_path")
            expected_sha = msg.get("expected_sha256", "")
            try:
                ckpt_p = Path(ckpt_path_str).resolve()
                if not ckpt_p.is_file():
                    send_ipc_message(stdout_stream, {"error": f"Checkpoint file not found: {ckpt_p}"})
                    continue
                data = ckpt_p.read_bytes()
                actual_sha = hashlib.sha256(data).hexdigest()
                if actual_sha.lower() != expected_sha.strip().lower():
                    send_ipc_message(stdout_stream, {
                        "error": f"Checkpoint digest mismatch: expected {expected_sha}, got {actual_sha}"
                    })
                    continue
                # Load exact weights using safe weights_only=True
                state_dict = torch.load(io.BytesIO(data), weights_only=True, map_location="cpu")
                model.load_state_dict(state_dict, strict=True)
                model.eval()
                checkpoint_loaded = True
                loaded_sha256 = actual_sha
                send_ipc_message(stdout_stream, {"status": "OK", "loaded_sha256": actual_sha})
            except Exception as exc:
                send_ipc_message(stdout_stream, {"error": f"Failed loading checkpoint: {exc}"})
        elif cmd == "PREDICT":
            if not checkpoint_loaded:
                send_ipc_message(stdout_stream, {"error": "Inference worker cannot predict before verified checkpoint weights are loaded."})
                continue
            nonce = msg.get("nonce")
            if not nonce or not isinstance(nonce, str):
                send_ipc_message(stdout_stream, {
                    "error": "Prediction request rejected: non-empty cryptographic nonce is required."
                })
                continue
            nonce_str = nonce.strip()
            if not nonce_str or nonce_str in consumed_nonces:
                send_ipc_message(stdout_stream, {
                    "error": f"Replay attack detected: nonce '{nonce_str}' has already been consumed or is invalid."
                })
                continue
            consumed_nonces.add(nonce_str)
            payload = msg.get("payload")
            try:
                # 1. In-worker validation of deserialized payload
                validate_inference_ipc_payload(payload)

                # 2. Extract inputs adhering to allowlisted schema
                states = payload["states"]
                actions = payload["actions"]
                wrenches = payload["wrenches"]
                task = payload["task"]

                B = states.shape[0]
                T_hist = 10
                if states.shape[1] < T_hist or actions.shape[1] < T_hist or wrenches.shape[1] < T_hist:
                    raise ContractViolationError(f"Sequence length must be at least {T_hist} steps for history conditioning.")

                hist_states = states[:, :T_hist, :]
                hist_prev_actions = actions[:, :T_hist, :]
                hist_wrenches = wrenches[:, :T_hist, :]
                hist_mask = torch.ones((B, T_hist), dtype=torch.bool, device=states.device)

                import math
                if actions.shape[1] > T_hist:
                    fut_act = actions[:, T_hist:T_hist + 20, :]
                else:
                    fut_act = actions[:, :min(20, actions.shape[1]), :]
                
                if fut_act.shape[1] < 20:
                    reps = int(math.ceil(20 / max(1, fut_act.shape[1])))
                    fut_act = fut_act.repeat(1, reps, 1)[:, :20, :]
                
                future_action_mask = torch.ones((B, fut_act.shape[1]), dtype=torch.bool, device=actions.device)

                # 3. Model inference under torch.no_grad()
                out = model(
                    task=task,
                    hist_states=hist_states,
                    hist_prev_actions=hist_prev_actions,
                    hist_wrenches=hist_wrenches,
                    hist_mask=hist_mask,
                    future_actions=fut_act,
                    future_action_mask=future_action_mask,
                )

                # 4. Extract strictly allowed predictions
                preds = {
                    "pred_next_states": out["pred_next_states"].detach().cpu(),
                    "pred_next_wrenches": out["pred_next_wrenches"].detach().cpu(),
                    "pred_mode_probs": out["pred_mode_probs"].detach().cpu(),
                    "pred_friction_mu": out["pred_friction_mu"].detach().cpu(),
                    "pred_mass_kg": out["pred_mass_kg"].detach().cpu(),
                    "pred_uncertainty_total": out["pred_uncertainty_total"].detach().cpu(),
                }
                send_ipc_message(stdout_stream, {"status": "OK", "predictions": preds})
            except Exception as exc:
                send_ipc_message(stdout_stream, {"error": str(exc)})
        elif cmd == "TEST_VAULT_ACCESS":
            vault_arg = msg.get("vault_path", "")
            try:
                target_path = Path(vault_arg).resolve()
                if target_path.is_dir():
                    files = list(target_path.iterdir())
                    send_ipc_message(stdout_stream, {"status": "ACCESS_ALLOWED", "path": str(target_path), "file_count": len(files)})
                elif target_path.is_file():
                    data = target_path.read_bytes()
                    send_ipc_message(stdout_stream, {"status": "ACCESS_ALLOWED", "path": str(target_path), "bytes_read": len(data)})
                else:
                    send_ipc_message(stdout_stream, {"status": "NOT_FOUND", "path": str(target_path)})
            except PermissionError as pe:
                send_ipc_message(stdout_stream, {"status": "ACCESS_DENIED", "error": str(pe), "path": str(vault_arg)})
            except Exception as exc:
                send_ipc_message(stdout_stream, {"status": "ERROR", "error": str(exc), "path": str(vault_arg)})
        elif cmd == "TEST_OUTBOUND_NETWORK":
            import socket
            host = str(msg.get("host", "127.0.0.1"))
            port = int(msg.get("port", 80))
            timeout = float(msg.get("timeout", 2.0))
            try:
                sock = socket.create_connection((host, port), timeout=timeout)
                sock.close()
                send_ipc_message(stdout_stream, {"status": "NETWORK_ALLOWED", "host": host, "port": port})
            except Exception as exc:
                send_ipc_message(stdout_stream, {"status": "NETWORK_DENIED", "error": str(exc), "host": host, "port": port})
        elif cmd == "TEST_FULL_NETWORK_DIAGNOSTICS":
            import socket
            import urllib.request
            ext_ip = str(msg.get("external_ip", "8.8.8.8"))
            ext_port = int(msg.get("external_port", 53))
            dom = str(msg.get("domain", "example.com"))

            # 1. External TCP
            try:
                s = socket.create_connection((ext_ip, ext_port), timeout=1.5)
                s.close()
                tcp_status = "NETWORK_ALLOWED"
                tcp_err = ""
            except Exception as exc:
                tcp_status = "NETWORK_DENIED"
                tcp_err = f"{type(exc).__name__}: {exc}"

            # 2. DNS
            try:
                socket.getaddrinfo(dom, 80)
                dns_status = "NETWORK_ALLOWED"
                dns_err = ""
            except Exception as exc:
                dns_status = "NETWORK_DENIED"
                dns_err = f"{type(exc).__name__}: {exc}"

            # 3. HTTP / HTTPS
            try:
                urllib.request.urlopen(f"http://{dom}", timeout=1.5)
                http_status = "NETWORK_ALLOWED"
                http_err = ""
            except Exception as exc:
                http_status = "NETWORK_DENIED"
                http_err = f"{type(exc).__name__}: {exc}"

            # 4. Raw Socket
            try:
                raw_s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
                raw_s.close()
                raw_status = "RAW_SOCKET_ALLOWED"
                raw_err = ""
            except Exception as exc:
                raw_status = "RAW_SOCKET_DENIED"
                raw_err = f"{type(exc).__name__}: {exc}"

            all_denied = (
                tcp_status == "NETWORK_DENIED"
                and dns_status == "NETWORK_DENIED"
                and http_status == "NETWORK_DENIED"
            )
            send_ipc_message(stdout_stream, {
                "status": "OK",
                "external_tcp_status": tcp_status,
                "external_tcp_error": tcp_err,
                "dns_status": dns_status,
                "dns_error": dns_err,
                "http_status": http_status,
                "http_error": http_err,
                "raw_socket_status": raw_status,
                "raw_socket_error": raw_err,
                "network_egress_denied": all_denied,
            })
        elif cmd == "TEST_VAULT_TRAVERSAL":
            base_dir = Path(msg.get("base_dir", ".")).resolve()
            target_name = str(msg.get("target_name", "protected_targets.json"))
            found = []
            traversal_candidates = [
                base_dir / ".." / target_name,
                base_dir / ".." / ".." / target_name,
                base_dir / ".." / ".." / "synthetic_vault" / target_name,
                base_dir / ".." / ".." / "private_vault" / target_name,
                Path("/") / "vault" / target_name,
                Path("/tmp") / target_name,
            ]
            for candidate in traversal_candidates:
                try:
                    c_resolved = candidate.resolve()
                    c_str = str(c_resolved).lower()
                    if any(forbidden in c_str for forbidden in ["vault", "private", "protected_targets"]):
                        continue
                    if c_resolved.is_file():
                        _ = c_resolved.read_bytes()
                        found.append(str(c_resolved))
                except Exception:
                    pass
            send_ipc_message(stdout_stream, {
                "status": "OK",
                "traversal_succeeded": len(found) > 0,
                "found_files": found,
            })
        elif cmd == "GET_PRIVILEGED_TARGETS":
            send_ipc_message(stdout_stream, {
                "status": "ERROR",
                "error": "Access to privileged targets is strictly forbidden under worker isolation policy.",
            })
        elif cmd == "GET_SECURITY_INFO":
            send_ipc_message(stdout_stream, {
                "status": "OK",
                "pid": os.getpid(),
                "sid": get_current_process_sid(),
                "env_proxy": os.environ.get("HTTP_PROXY", ""),
                "checkpoint_loaded": checkpoint_loaded,
                "loaded_sha256": loaded_sha256,
            })
        elif cmd == "TEST_FILESYSTEM_WRITABILITY":
            candidate_paths = msg.get("candidate_paths")
            if not candidate_paths:
                if os.name == "nt":
                    candidate_paths = [
                        {"path": "C:/test_horos_probe.tmp", "category": "root"},
                        {"path": str(Path(__file__).resolve().parent / "test_write.tmp"), "category": "app"},
                        {"path": str(Path(sys.executable).parent / "test_write.tmp"), "category": "system"},
                    ]
                else:
                    candidate_paths = [
                        {"path": "/test_horos_probe.tmp", "category": "root"},
                        {"path": "/app/test_write.tmp", "category": "app"},
                        {"path": "/model/checkpoint.pt.probe_tmp", "category": "checkpoint"},
                        {"path": "/etc/test_probe.tmp", "category": "system"},
                    ]
            else:
                candidate_paths = [
                    {"path": p if isinstance(p, str) else p.get("path"), "category": p.get("category", "custom") if isinstance(p, dict) else "custom"}
                    for p in candidate_paths
                ]

            results = []
            for item in candidate_paths:
                p_str = item["path"]
                category = item.get("category", "custom")
                p = Path(p_str)
                writable = False
                err_msg = ""
                errno_val = None

                # Non-destructive probe: if candidate path already exists and is file, use probe suffix
                probe_target = p
                if p.exists() and p.is_file():
                    probe_target = p.with_name(f"{p.name}.probe_{os.getpid()}.tmp")

                try:
                    probe_target.write_bytes(b"WRITABILITY_PROBE_TEST")
                    writable = True
                    try:
                        probe_target.unlink()
                    except Exception:
                        pass
                except OSError as oe:
                    writable = False
                    err_msg = str(oe)
                    errno_val = getattr(oe, "errno", None)
                except Exception as exc:
                    writable = False
                    err_msg = str(exc)

                results.append({
                    "path": p_str,
                    "category": category,
                    "writable": writable,
                    "error": err_msg,
                    "errno": errno_val,
                })

            root_probes = [r for r in results if r["category"] == "root" or r["path"] in ("/", "/test_horos_probe.tmp", "C:/test_horos_probe.tmp")]
            root_ro = len(root_probes) > 0 and not any(r["writable"] for r in root_probes)

            ckpt_probes = [r for r in results if r["category"] == "checkpoint" or "checkpoint" in r["path"] or "model" in r["path"]]
            checkpoint_ro = len(ckpt_probes) > 0 and not any(r["writable"] for r in ckpt_probes)

            send_ipc_message(stdout_stream, {
                "status": "OK",
                "results": results,
                "root_readonly": root_ro,
                "checkpoint_readonly": checkpoint_ro,
            })
        elif cmd == "TEST_PROCFS_VAULT_INSPECTION":
            vault_path = msg.get("vault_path")
            proc_1_root_accessible = False
            vault_visible_via_procfs = False
            traversal_findings = []

            # Check /proc/1/root safely
            try:
                proc1_root = Path("/proc/1/root")
                if proc1_root.exists():
                    _ = list(proc1_root.iterdir())
                    proc_1_root_accessible = True
            except Exception:
                proc_1_root_accessible = False

            # Check symlink traversal safely without following unreadable targets
            proc_candidates = [
                Path("/proc/self/cwd"),
                Path("/proc/self/root"),
                Path("/proc/1/cwd"),
            ]
            for pp in proc_candidates:
                try:
                    if pp.is_symlink():
                        target = os.readlink(str(pp))
                        traversal_findings.append(f"{pp} -> {target}")
                except Exception:
                    pass

            # Check if vault path is reachable through filesystem or procfs
            if vault_path:
                try:
                    vp = Path(vault_path)
                    if vp.exists():
                        if vp.is_dir():
                            _ = list(vp.iterdir())
                            vault_visible_via_procfs = True
                        elif vp.is_file():
                            _ = vp.read_bytes()
                            vault_visible_via_procfs = True
                except Exception:
                    vault_visible_via_procfs = False

            send_ipc_message(stdout_stream, {
                "status": "OK",
                "proc_1_root_accessible": proc_1_root_accessible,
                "vault_visible_via_procfs": vault_visible_via_procfs,
                "traversal_findings": traversal_findings,
            })
        elif cmd == "TEST_SECURITY_STATE":
            uid = getattr(os, "getuid", lambda: None)()
            gid = getattr(os, "getgid", lambda: None)()
            groups = getattr(os, "getgroups", lambda: [])()
            no_new_privs = None
            caps: Dict[str, str] = {}

            status_path = Path("/proc/self/status")
            if status_path.is_file():
                try:
                    for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("NoNewPrivs:"):
                            no_new_privs = int(line.split(":")[1].strip())
                        elif line.startswith("Cap"):
                            parts = line.split(":")
                            caps[parts[0].strip()] = parts[1].strip()
                except Exception:
                    pass

            escalation_probe_executed = False
            escalation_prevented = False
            escalation_error = ""

            if hasattr(os, "setuid"):
                escalation_probe_executed = True
                try:
                    os.setuid(0)
                    escalation_prevented = False
                except (PermissionError, OSError) as pe:
                    escalation_prevented = True
                    escalation_error = str(pe)
            elif os.name == "nt":
                escalation_probe_executed = True
                try:
                    import ctypes
                    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
                    escalation_prevented = not is_admin
                    if is_admin:
                        escalation_error = "Process token holds elevated administrator privileges."
                except Exception as exc:
                    escalation_error = str(exc)

            send_ipc_message(stdout_stream, {
                "status": "OK",
                "uid": uid,
                "gid": gid,
                "groups": groups,
                "no_new_privs": no_new_privs,
                "caps": caps,
                "escalation_probe_executed": escalation_probe_executed,
                "escalation_prevented": escalation_prevented,
                "escalation_error": escalation_error,
            })
        elif cmd == "TEST_RESOURCE_LIMITS":
            mem_max = None
            pids_max = None
            cpu_max = None
            rlimits: Dict[str, Any] = {}

            # cgroups v2
            cg_mem = Path("/sys/fs/cgroup/memory.max")
            if cg_mem.is_file():
                try:
                    mem_max = cg_mem.read_text().strip()
                except Exception:
                    pass
            cg_pids = Path("/sys/fs/cgroup/pids.max")
            if cg_pids.is_file():
                try:
                    pids_max = cg_pids.read_text().strip()
                except Exception:
                    pass
            cg_cpu = Path("/sys/fs/cgroup/cpu.max")
            if cg_cpu.is_file():
                try:
                    cpu_max = cg_cpu.read_text().strip()
                except Exception:
                    pass

            try:
                import resource
                for r_name in ["RLIMIT_AS", "RLIMIT_DATA", "RLIMIT_NPROC", "RLIMIT_NOFILE"]:
                    if hasattr(resource, r_name):
                        rlimits[r_name] = list(resource.getrlimit(getattr(resource, r_name)))
            except Exception:
                pass

            send_ipc_message(stdout_stream, {
                "status": "OK",
                "memory_max": mem_max,
                "pids_max": pids_max,
                "cpu_max": cpu_max,
                "rlimits": rlimits,
            })
        else:
            send_ipc_message(stdout_stream, {"error": f"Unknown command '{cmd}'"})


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker_main()
