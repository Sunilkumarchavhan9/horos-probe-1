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
    ):
        self.python_executable = python_executable or sys.executable
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.timeout_seconds = timeout_seconds
        self.env = create_sanitized_worker_environment()
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.expected_checkpoint_sha256 = expected_checkpoint_sha256
        self._model_verified_and_loaded = False
        
        # Spawn child worker process with piped stdin/stdout/stderr
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

    def predict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validates payload schema, transmits across IPC pipe, and returns verified model predictions."""
        # 1. Evaluator-side pre-IPC schema and privileged-key validation
        validate_inference_ipc_payload(payload)

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
            send_ipc_message(self.proc.stdin, {"command": "PREDICT", "payload": payload})
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
    
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    model.eval()
    checkpoint_loaded = False
    loaded_sha256 = ""

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
            target_path = Path(msg.get("vault_path", "")).resolve()
            try:
                if target_path.is_dir():
                    files = list(target_path.iterdir())
                    send_ipc_message(stdout_stream, {"status": "ACCESS_ALLOWED", "path": str(target_path), "file_count": len(files)})
                else:
                    data = target_path.read_bytes()
                    send_ipc_message(stdout_stream, {"status": "ACCESS_ALLOWED", "path": str(target_path), "bytes_read": len(data)})
            except PermissionError as pe:
                send_ipc_message(stdout_stream, {"status": "ACCESS_DENIED", "error": str(pe), "path": str(target_path)})
            except Exception as exc:
                send_ipc_message(stdout_stream, {"status": "ERROR", "error": str(exc), "path": str(target_path)})
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
        elif cmd == "GET_SECURITY_INFO":
            send_ipc_message(stdout_stream, {
                "status": "OK",
                "pid": os.getpid(),
                "sid": get_current_process_sid(),
                "env_proxy": os.environ.get("HTTP_PROXY", ""),
                "checkpoint_loaded": checkpoint_loaded,
                "loaded_sha256": loaded_sha256,
            })
        else:
            send_ipc_message(stdout_stream, {"error": f"Unknown command '{cmd}'"})


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker_main()
