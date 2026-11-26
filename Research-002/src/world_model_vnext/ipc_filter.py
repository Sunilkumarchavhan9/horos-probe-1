"""Strict IPC schema validation, recursive forbidden-key filtering, and Windows isolation assertions.

Implements Stage 3.7A Workstream C:
- Enforces strict allowlist on inference worker inputs: ONLY {states, actions, wrenches, task}.
- Deep recursive traversal to detect and block any nested forbidden keys, privileged targets, or metadata objects.
- Validates data types, tensor ranks, task-specific channel dimensions, and numeric validity (no NaN/Inf).
- Validates filesystem vault path denial and network access restriction assertions.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Union

import numpy as np
import torch

from .contracts import (
    FORBIDDEN_MODEL_INPUT_KEYS,
    TASK_INTERFACES,
    ContractViolationError,
)

# Strict allowlist of permitted top-level payload keys
PERMITTED_INFERENCE_PAYLOAD_KEYS: FrozenSet[str] = frozenset({
    "states",
    "actions",
    "wrenches",
    "task",
})

# Forbidden substring tokens in any key name anywhere in the payload
FORBIDDEN_KEY_SUBSTRING_TOKENS: Tuple[str, ...] = (
    "mu_true",
    "target_mu",
    "step_mu",
    "friction_mu",
    "mass_kg",
    "target_mass",
    "step_mass",
    "object_mass",
    "clearance",
    "contact_mode",
    "target_mode",
    "modes",
    "step_contact_stratum",
    "seed",
    "episode_id",
    "canonical_episode_id",
    "slot_alias",
    "provenance_hash",
    "rewards",
    "env_metadata",
    "privileged",
)


def recursive_assert_no_privileged_keys(obj: Any, path: str = "root") -> None:
    """Recursively traverses dictionaries, lists, and tuples to reject forbidden privileged keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_str = str(k).strip()
            lowered = k_str.lower()
            current_path = f"{path}.{k_str}"

            if lowered in FORBIDDEN_MODEL_INPUT_KEYS:
                raise ContractViolationError(
                    f"Forbidden privileged key '{k_str}' found at path '{current_path}'."
                )
            for token in FORBIDDEN_KEY_SUBSTRING_TOKENS:
                if token in lowered:
                    raise ContractViolationError(
                        f"Key '{k_str}' at path '{current_path}' matches forbidden token '{token}'."
                    )
            recursive_assert_no_privileged_keys(v, current_path)
    elif isinstance(obj, (list, tuple, set)):
        for idx, item in enumerate(obj):
            recursive_assert_no_privileged_keys(item, f"{path}[{idx}]")


def validate_inference_ipc_payload(payload: Dict[str, Any]) -> None:
    """Validates that an inference IPC payload strictly adheres to the allowlist schema.
    
    Rejects unknown fields, verifies tensor types, ranks, task-specific dimensions, and numerical validity.
    """
    if not isinstance(payload, dict):
        raise ContractViolationError("Inference IPC payload must be a dictionary.")

    # 1. Recursive check for forbidden keys anywhere in the structure
    recursive_assert_no_privileged_keys(payload)

    # 2. Strict allowlist check: reject ANY unexpected top-level key
    payload_keys = set(payload.keys())
    unknown_keys = payload_keys - PERMITTED_INFERENCE_PAYLOAD_KEYS
    if unknown_keys:
        raise ContractViolationError(
            f"Inference IPC payload contains unauthorized unknown keys: {sorted(unknown_keys)}. "
            f"Allowed schema permits only: {sorted(PERMITTED_INFERENCE_PAYLOAD_KEYS)}."
        )

    # 3. Check presence of all required fields
    missing_keys = PERMITTED_INFERENCE_PAYLOAD_KEYS - payload_keys
    if missing_keys:
        raise ContractViolationError(f"Inference IPC payload missing required fields: {sorted(missing_keys)}.")

    # 4. Validate 'task'
    task = payload["task"]
    if not isinstance(task, str):
        raise ContractViolationError(f"'task' must be a string, got {type(task)}.")
    if task not in TASK_INTERFACES:
        raise ContractViolationError(
            f"Unsupported task '{task}'. Permitted tasks: {sorted(TASK_INTERFACES.keys())}."
        )
    task_spec = TASK_INTERFACES[task]

    # Helper for tensor validation
    def _validate_tensor(name: str, arr: Any, expected_dim: int, allow_t_minus_1: bool = False) -> Tuple[int, int]:
        if not isinstance(arr, (torch.Tensor, np.ndarray)):
            raise ContractViolationError(f"Field '{name}' must be a torch.Tensor or numpy.ndarray, got {type(arr)}.")
        
        shape = tuple(arr.shape)
        if len(shape) != 3:
            raise ContractViolationError(f"Field '{name}' must have rank 3 (B, T, D), got shape {shape}.")
        
        b, t, d = shape
        if d != expected_dim:
            raise ContractViolationError(
                f"Field '{name}' channel dimension mismatch for task '{task}': "
                f"expected {expected_dim}, got {d} (full shape: {shape})."
            )

        # Numerical validity (check for NaN / Inf)
        if isinstance(arr, torch.Tensor):
            if not torch.isfinite(arr).all().item():
                raise ContractViolationError(f"Field '{name}' contains non-finite values (NaN or Inf).")
        else:
            if not np.all(np.isfinite(arr)):
                raise ContractViolationError(f"Field '{name}' contains non-finite values (NaN or Inf).")

        return b, t

    # 5. Validate tensors
    b_s, t_s = _validate_tensor("states", payload["states"], task_spec.state_dim)
    b_a, t_a = _validate_tensor("actions", payload["actions"], task_spec.action_dim, allow_t_minus_1=True)
    b_w, t_w = _validate_tensor("wrenches", payload["wrenches"], task_spec.wrench_dim)

    # Validate batch size alignment
    if not (b_s == b_a == b_w):
        raise ContractViolationError(
            f"Batch size mismatch across input tensors: states={b_s}, actions={b_a}, wrenches={b_w}."
        )

    # Validate temporal length alignment (actions can be T or T-1)
    if not (t_s == t_w and (t_a == t_s or t_a == t_s - 1)):
        raise ContractViolationError(
            f"Temporal dimension mismatch: states T={t_s}, wrenches T={t_w}, actions T={t_a}."
        )


def assert_worker_path_access_permitted(requested_path: Path | str, private_vault_root: Path | str) -> None:
    """Verifies that an inference worker process cannot access paths within the private holdout vault."""
    req = Path(requested_path).resolve()
    vault = Path(private_vault_root).resolve()
    
    # Check if requested_path is within or equal to vault_root
    try:
        req.relative_to(vault)
        # If relative_to succeeds, the path is inside the vault!
        raise PermissionError(
            f"Access denied: Worker process cannot read from private holdout vault '{req}'."
        )
    except ValueError:
        # Path is outside the vault
        pass


def assert_no_external_network_call(destination_host: str) -> None:
    """Asserts that the inference worker cannot establish outbound network connections."""
    norm = destination_host.strip().lower()
    allowed_loopbacks = {"localhost", "127.0.0.1", "::1", "pipe"}
    if norm not in allowed_loopbacks:
        raise PermissionError(
            f"Network access denied: Outbound connection to '{destination_host}' is blocked by sandbox policy."
        )
