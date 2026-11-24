"""Local allocator plus the archived revision helpers under the same module name."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

_REVISION = Path(__file__).resolve().parent.parent / "feasibility_aware_recovery_p5_t4_revision" / "dynamic_force_allocator.py"
_SPEC = importlib.util.spec_from_file_location("p5t4_revision_force_allocator", _REVISION)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
compose_command = _MODULE.compose_command
geometry_targets = _MODULE.geometry_targets

FN_MAX = 25.0


def allocate(normals, valid: Sequence[bool], w_ext, per_contact: float, budget: Optional[float]) -> np.ndarray:
    count = len(valid)
    cmd = np.zeros(count)
    load = -np.asarray(w_ext[:2], dtype=float)
    norm = float(np.linalg.norm(load))
    direction = load / norm if norm > 1e-8 else np.array([0.0, 1.0])
    weights = np.zeros(count)
    for index, is_valid in enumerate(valid):
        if not is_valid:
            continue
        normal = np.asarray(normals[index], dtype=float)
        weights[index] = 0.25 + max(0.0, float(np.dot(normal, direction)))
    active = int(np.sum(weights > 0))
    if active == 0:
        return cmd
    total = float(per_contact) * active
    if budget is not None:
        total = min(total, float(budget))
    cmd = weights / np.sum(weights) * total
    return np.clip(cmd, 0.0, FN_MAX)
