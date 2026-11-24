"""Choose the smallest force trajectory the rate-limited rollout can survive."""

from __future__ import annotations

import _paths  # noqa: F401
from typing import Dict, Optional, Sequence

import numpy as np

from dynamic_force_allocator import compose_command, geometry_targets
from rate_limited_dynamics import rollout


def _mass_from_wrench(w_ext: np.ndarray, radius: float) -> tuple:
    mass = float(np.clip(-float(w_ext[1]) / 9.81, 0.2, 0.8))
    inertia = 0.5 * mass * radius * radius
    return mass, inertia


def candidate_commands(
    obs: Dict[str, object],
    static_fn: Optional[Sequence[float]],
    mode: str,
    budget: Optional[float],
) -> list:
    valid = [bool(v) for v in obs["valid"]]
    applied = np.asarray(obs["fn_applied"], dtype=float)
    active = max(1, sum(1 for v in valid if v))
    static = np.zeros(len(valid)) if static_fn is None else np.asarray(static_fn, dtype=float)
    static_sum = max(1.0, float(np.sum(static)))
    current_sum = max(0.0, float(sum(applied[i] for i, v in enumerate(valid) if v)))
    if mode == "geometry_squeeze":
        totals = [active * 10.0]
    elif mode == "fixed_margin" or mode == "static_only":
        totals = [max(current_sum, 1.8 * static_sum)]
    else:
        totals = [
            max(current_sum, static_sum),
            max(current_sum, 1.6 * static_sum),
            max(current_sum, min(active * 12.0, 2.4 * static_sum)),
        ]
    commands = []
    for total in totals:
        if budget is not None:
            total = min(float(total), float(budget))
        shares = geometry_targets(
            obs["normal"], obs["mu_hat"], valid, obs["external_wrench_hat"],
            obs["object_velocity"], float(obs["object_omega"]), obs["position"], total,
        )
        cmd = compose_command(applied, shares, obs["normal"], valid, obs["external_wrench_hat"], budget)
        commands.append(cmd)
    return commands


def select_command(obs: Dict[str, object], static_fn: Optional[Sequence[float]], mode: str, budget: Optional[float]) -> Dict[str, object]:
    commands = candidate_commands(obs, static_fn, mode, budget)
    if mode != "min_surviving" or len(commands) == 1:
        chosen = commands[-1]
        return {"cmd": chosen, "predicted_survival": None, "candidates": len(commands)}
    w_ext = np.asarray(obs["external_wrench_hat"], dtype=float)
    mass, inertia = _mass_from_wrench(w_ext, float(obs["radius"]))
    valid = [bool(v) for v in obs["valid"]]
    ranked = []
    for cmd in commands:
        result = rollout(
            fn=obs["fn_applied"],
            target=cmd,
            normals=obs["normal"],
            tangents=obs["tangent"],
            positions=obs["position"],
            mu=obs["mu_hat"],
            valid=valid,
            w_ext=w_ext,
            position=obs["object_position"],
            velocity=obs["object_velocity"],
            theta=float(obs["object_theta"]),
            omega=float(obs["object_omega"]),
            mass=mass,
            inertia=inertia,
            dt=float(obs["dt"]),
            steps=12,
        )
        ranked.append((bool(result["survived"]), -float(np.sum(cmd)), int(result["steps_to_boundary"]), cmd, result))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best = ranked[0]
    return {
        "cmd": best[3],
        "predicted_survival": bool(best[4]["survived"]),
        "candidates": len(commands),
        "steps_to_boundary": int(best[4]["steps_to_boundary"]),
    }
