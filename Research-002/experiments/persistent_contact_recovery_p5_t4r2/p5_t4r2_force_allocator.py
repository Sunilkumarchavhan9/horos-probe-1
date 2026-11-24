"""Allocate a fixed total onto the contacts whose normals oppose the estimated load."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

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
    if float(np.sum(weights)) <= 0.0:
        return cmd
    total = float(per_contact) * int(np.sum(np.asarray(valid, dtype=bool)))
    if budget is not None:
        total = min(total, float(budget))
    cmd = weights / np.sum(weights) * total
    return np.clip(cmd, 0.0, FN_MAX)
