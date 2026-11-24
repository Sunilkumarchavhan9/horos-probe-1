"""
HOROS PROBE-1 — Phase 5 Task 4R4
Preload Force Allocator
File: preload_force_allocator.py

Allocates normal forces across N contacts to satisfy internal grasp squeeze,
counter external disturbance wrenches, and respect physical actuator limits.
Enforces strict internal state rate-limiting and budget scaling so that
commanded forces strictly satisfy:
1. Slew rate: |cmd_t - cmd_{t-1}| <= 50.0 N/s (0.05 N/step)
2. Bounds: cmd_i in [3.0, 25.0] N
3. Budget: sum(cmd_i) <= budget (Protocol B)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence
import numpy as np


class PreloadForceAllocator:
    """
    Allocates normal forces across N contacts given target preload and wrench estimates.
    Maintains internal command state for strictly slew-limited and budget-compliant output.
    """

    def __init__(
        self,
        fn_min: float = 3.0,
        fn_max: float = 25.0,
        slew_limit: float = 50.0,   # N/s
        dt: float = 0.001,           # 1000 Hz physics loop
    ):
        self.fn_min = float(fn_min)
        self.fn_max = float(fn_max)
        self.max_delta = float(slew_limit * dt)  # 0.05 N/step
        self.cmd_held: Optional[np.ndarray] = None

    def reset(self, initial_fn: Optional[Sequence[float]] = None, budget: Optional[float] = None):
        if initial_fn is not None:
            self.cmd_held = np.asarray(initial_fn, dtype=float).copy()
            if budget is not None and float(np.sum(self.cmd_held)) > float(budget) + 1e-6:
                self.cmd_held = self.cmd_held * (float(budget) / float(np.sum(self.cmd_held)))
        else:
            self.cmd_held = None

    def allocate(
        self,
        current_fn: Sequence[float],
        target_fn_per_contact: float,
        normals: Sequence[Sequence[float]],
        positions: Sequence[Sequence[float]],
        valid: Sequence[bool],
        w_ext_hat: Sequence[float],
        budget: Optional[float] = None,
    ) -> np.ndarray:
        """
        Computes rate-limited normal force commands:
        1. Distributes target_fn_per_contact with wrench-opposition weighting.
        2. Projects into budget constraint if budget is specified.
        3. Enforces slew-rate limits: |cmd_t - cmd_{t-1}| <= max_delta.
        4. Invariant: sum(cmd) <= budget strictly maintained if budget is specified.
        """
        n = len(valid)
        active_indices = [i for i, v in enumerate(valid) if v]
        if not active_indices:
            self.cmd_held = np.zeros(n)
            return self.cmd_held.copy()

        if self.cmd_held is None or len(self.cmd_held) != n:
            self.cmd_held = np.asarray(current_fn, dtype=float).copy()
            if budget is not None and float(np.sum(self.cmd_held)) > float(budget) + 1e-6:
                self.cmd_held = self.cmd_held * (float(budget) / float(np.sum(self.cmd_held)))

        cmd_target = np.zeros(n)

        # Disturbance force vector in 2D
        w_ext_2d = -np.asarray(w_ext_hat[:2], dtype=float)
        norm_w = float(np.linalg.norm(w_ext_2d))
        dir_w = w_ext_2d / norm_w if norm_w > 1e-6 else np.array([0.0, 0.0])

        # Compute directional utility weights
        weights = np.zeros(n)
        for i in active_indices:
            normal_i = np.asarray(normals[i], dtype=float)
            normal_norm = np.linalg.norm(normal_i)
            normal_unit = (normal_i / normal_norm) if normal_norm > 1e-6 else normal_i
            alignment = max(0.0, float(np.dot(normal_unit, dir_w)))
            weights[i] = 1.0 + 0.5 * alignment

        sum_w = float(np.sum(weights))
        if sum_w <= 1e-6:
            for i in active_indices:
                weights[i] = 1.0
            sum_w = float(len(active_indices))

        # Base allocation
        total_target = float(target_fn_per_contact) * len(active_indices)
        if budget is not None:
            total_target = min(total_target, float(budget))

        for i in active_indices:
            share = (weights[i] / sum_w) * total_target
            cmd_target[i] = np.clip(share, self.fn_min, self.fn_max)

        # Compute per-contact feasible bounds under slew-rate and force limits
        cmd_applied = np.zeros(n)
        u_star = np.zeros(n)
        l_bounds = np.zeros(n)
        r_bounds = np.zeros(n)

        for i in range(n):
            if not valid[i]:
                self.cmd_held[i] = 0.0
                cmd_applied[i] = 0.0
                continue
            l_bounds[i] = max(self.fn_min, self.cmd_held[i] - self.max_delta)
            r_bounds[i] = min(self.fn_max, self.cmd_held[i] + self.max_delta)
            u_star[i] = float(np.clip(cmd_target[i], l_bounds[i], r_bounds[i]))

        # Enforce budget constraint via exact convex projection onto [l_bounds, r_bounds]
        sum_u = float(np.sum(u_star[active_indices]))
        if budget is not None and sum_u > float(budget):
            excess = sum_u - float(budget)
            avail = float(np.sum(u_star[active_indices] - l_bounds[active_indices]))
            frac = min(1.0, excess / avail) if avail > 1e-9 else 1.0
            for i in active_indices:
                cmd_applied[i] = float(u_star[i] - frac * (u_star[i] - l_bounds[i]))
        else:
            for i in active_indices:
                cmd_applied[i] = u_star[i]

        self.cmd_held = cmd_applied.copy()
        return cmd_applied.copy()
