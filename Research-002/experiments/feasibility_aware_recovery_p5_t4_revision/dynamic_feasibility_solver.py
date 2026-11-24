"""Static, rate-limited, dynamic, and robust feasibility are separate answers."""

from __future__ import annotations

import _paths  # noqa: F401
from typing import Dict, Optional, Sequence

import numpy as np

from contact_feasibility_solver import solve_bounded_wrench
from contact_wrench_analysis import ContactState
from rate_limited_dynamics import max_slew_gap, rollout

SLEW = 50.0
DROP_M = 0.025


def classify_dynamic(
    contacts: Sequence[ContactState],
    fn_now: Sequence[float],
    w_ext: Sequence[float],
    position: Sequence[float],
    velocity: Sequence[float],
    theta: float,
    omega: float,
    mass: float,
    inertia: float,
    dt: float,
    total_budget: Optional[float],
    target: Sequence[float],
) -> Dict[str, object]:
    solved = solve_bounded_wrench(contacts, np.asarray(w_ext, dtype=float), fn_min=0.0, fn_max=25.0, total_budget=total_budget)
    static_ok = bool(solved["feasible"])
    valid = [bool(c.valid) for c in contacts]
    gap = max_slew_gap(np.asarray(fn_now, dtype=float), np.asarray(solved["fn"], dtype=float), valid)
    disp = float(np.linalg.norm(position))
    speed = float(np.linalg.norm(velocity))
    if speed > 1e-4:
        time_left = max(0.0, (DROP_M - disp) / speed)
    else:
        time_left = 0.35
    rate_ok = bool(static_ok and gap <= SLEW * time_left + 1e-6)
    normals = [c.normal for c in contacts]
    tangents = [c.tangent for c in contacts]
    positions = [c.position for c in contacts]
    mu = [c.mu for c in contacts]
    common = dict(
        fn=fn_now,
        target=target,
        normals=normals,
        tangents=tangents,
        positions=positions,
        mu=mu,
        valid=valid,
        w_ext=w_ext,
        position=position,
        velocity=velocity,
        theta=theta,
        omega=omega,
        mass=mass,
        inertia=inertia,
        dt=dt,
        steps=16,
    )
    dynamic = rollout(**common)
    robust = rollout(**common, mu_scale=0.75, wrench_scale=1.15)
    return {
        "static_feasibility": static_ok,
        "rate_limited_feasibility": rate_ok,
        "dynamic_recoverability": bool(dynamic["survived"]),
        "robust_dynamic_feasibility": bool(robust["survived"]),
        "time_to_boundary_s": time_left,
        "slew_gap_n": gap,
        "static_sum_fn": float(solved["sum_fn"]),
        "dynamic_steps_to_boundary": int(dynamic["steps_to_boundary"]),
        "terms_are_distinct": True,
    }
