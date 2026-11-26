"""Operating-system level inference worker isolation, NTFS ACL enforcement, and IPC client.

Implements Stage 3.7B Phases D, E, F:
- Real Windows OS isolation using NTFS ACL deny rules on private holdout vault paths.
- Stripped environment and network isolation blackholing for child worker processes.
- Bidirectional IPC payload schema validation (evaluator -> IPC pipe -> isolated worker).
- Fail-closed worker execution with strict privilege separation.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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


def apply_os_vault_isolation(vault_path: Path | str) -> bool:
    """Applies real operating-system filesystem access denial to a private vault directory.
    
    On Windows: Configures NTFS Deny ACL for the current user and child processes using icacls.
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


def send_ipc_message(stream: Any, obj: Any) -> None:
    """Serializes and sends an object across a binary IPC stream with length prefix."""
    buf = io.BytesIO()
    torch.save(obj, buf)
    data = buf.getvalue()
    stream.write(len(data).to_bytes(8, byteorder="big"))
    stream.write(data)
    stream.flush()


def recv_ipc_message(stream: Any) -> Any:
    """Reads and deserializes a length-prefixed object from a binary IPC stream."""
    header = stream.read(8)
    if not header or len(header) < 8:
        raise EOFError("IPC pipe closed by remote endpoint.")
    length = int.from_bytes(header, byteorder="big")
    data = stream.read(length)
    if len(data) < length:
        raise EOFError(f"Truncated IPC payload: expected {length} bytes, received {len(data)}.")
    buf = io.BytesIO(data)
    return torch.load(buf, weights_only=False)


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

    # Outbound network blackhole configuration
    env["HTTP_PROXY"] = "http://0.0.0.0:0"
    env["HTTPS_PROXY"] = "http://0.0.0.0:0"
    env["ALL_PROXY"] = "http://0.0.0.0:0"
    env["NO_PROXY"] = ""
    env["HOROS_ISOLATED_WORKER"] = "1"

    # Ensure PYTHONPATH includes the src root directory so worker child process always finds world_model_vnext
    src_dir = str(Path(__file__).resolve().parent.parent)
    existing_pp = env.get("PYTHONPATH", "")
    if src_dir not in existing_pp:
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir

    return env


class IsolatedInferenceWorker:
    """Evaluator-side client managing an OS-isolated inference child process."""

    def __init__(
        self,
        python_executable: Optional[str] = None,
        working_dir: Optional[Path | str] = None,
        timeout_seconds: float = 30.0,
    ):
        self.python_executable = python_executable or sys.executable
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.timeout_seconds = timeout_seconds
        self.env = create_sanitized_worker_environment()
        
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

    def predict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validates payload schema, transmits across IPC pipe, and returns verified model predictions."""
        if self._closed or self.proc.poll() is not None:
            stderr_out = self.proc.stderr.read().decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise ContractViolationError(
                f"Inference worker process has terminated unexpectedly (exit code {self.proc.poll()}). "
                f"Worker stderr: {stderr_out.strip()}"
            )

        # 1. Evaluator-side pre-IPC schema and privileged-key validation
        validate_inference_ipc_payload(payload)

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
    # Enforce evaluation-only mode (zero gradient computation)
    torch.set_grad_enabled(False)
    
    # Initialize the model using locked architecture config in eval mode
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    model.eval()

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
        elif cmd == "PREDICT":
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

                # Future actions: steps following history, clamped to max_future_horizon (20)
                if actions.shape[1] > T_hist:
                    fut_act = actions[:, T_hist:T_hist + 20, :]
                else:
                    fut_act = actions[:, :min(20, actions.shape[1]), :]
                
                H_fut = fut_act.shape[1]
                if H_fut < 1:
                    # Default 1-step future action if none remaining
                    fut_act = actions[:, -1:, :]
                    H_fut = 1
                future_action_mask = torch.ones((B, H_fut), dtype=torch.bool, device=actions.device)

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

                # 3. Extract strictly allowed predictions
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
        else:
            send_ipc_message(stdout_stream, {"error": f"Unknown command '{cmd}'"})


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker_main()
