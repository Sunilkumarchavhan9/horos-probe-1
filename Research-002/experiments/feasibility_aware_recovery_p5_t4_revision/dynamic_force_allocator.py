"""Normal-force targets from causal geometry. The plant still chooses friction."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

FN_MAX = 25.0


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        return np.array([0.0, 1.0])
    return vector / norm


def geometry_targets(
    normals,
    mu_hat,
    valid: Sequence[bool],
    w_ext: Sequence[float],
    velocity: Sequence[float],
    omega: float,
    positions,
    total_force: float,
) -> np.ndarray:
    n = len(valid)
    load = -np.asarray(w_ext[:2], dtype=float)
    oppose = -np.asarray(velocity, dtype=float)
    weights = np.zeros(n)
    for i, is_valid in enumerate(valid):
        if not is_valid:
            continue
        normal = np.asarray(normals[i], dtype=float)
        position = np.asarray(positions[i], dtype=float)
        useful = max(0.0, float(np.dot(normal, _unit(load))))
        brake = max(0.0, float(np.dot(normal, _unit(oppose))))
        torque_arm = float(position[0] * normal[1] - position[1] * normal[0])
        spin = max(0.0, torque_arm * (-float(omega)))
        weights[i] = (0.20 + useful) * (0.35 + max(0.05, float(mu_hat[i]))) + 0.45 * brake + 8.0 * spin
    if float(np.sum(weights)) <= 1e-8:
        for i, is_valid in enumerate(valid):
            weights[i] = 1.0 if is_valid else 0.0
    if float(np.sum(weights)) <= 1e-8:
        return np.zeros(n)
    shares = weights / np.sum(weights) * max(0.0, float(total_force))
    return np.clip(shares, 0.0, FN_MAX)


def compose_command(
    applied: Sequence[float],
    shares: np.ndarray,
    normals,
    valid: Sequence[bool],
    w_ext: Sequence[float],
    budget: Optional[float],
) -> np.ndarray:
    """Keep force on contacts that oppose the load. Allow the others to fall toward the share."""
    load = _unit(-np.asarray(w_ext[:2], dtype=float))
    cmd = np.array(shares, dtype=float).copy()
    applied_arr = np.asarray(applied, dtype=float)
    for i, is_valid in enumerate(valid):
        if not is_valid:
            cmd[i] = 0.0
            continue
        normal = np.asarray(normals[i], dtype=float)
        if float(np.dot(normal, load)) > 0.15:
            cmd[i] = max(float(cmd[i]), float(applied_arr[i]))
        cmd[i] = float(np.clip(cmd[i], 0.0, FN_MAX))
    if budget is not None and float(np.sum(cmd)) > float(budget):
        active = [i for i, is_valid in enumerate(valid) if is_valid]
        excess = float(np.sum(cmd)) - float(budget)
        reducible = [i for i in active if cmd[i] > 0.0]
        guard = 0
        while excess > 1e-6 and reducible and guard < 8:
            guard += 1
            room = sum(cmd[i] for i in reducible)
            if room <= 1e-8:
                break
            for i in reducible:
                cut = excess * (cmd[i] / room)
                cmd[i] = max(0.0, cmd[i] - cut)
            excess = float(sum(cmd[i] for i in active)) - float(budget)
    return cmd
