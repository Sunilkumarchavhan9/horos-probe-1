"""
Constrained contact feasibility. A solver status is accepted only after the
residual and the friction, unilateral, and actuator inequalities are checked.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linprog

from contact_wrench_analysis import (
    ContactState,
    equilibrium_residual,
    symmetric_pair_horizontal_margin,
)

FN_MAX_DEFAULT = 25.0
RESIDUAL_TOL = 1e-5
CONSTRAINT_TOL = 1e-6


def _active(contacts: Sequence[ContactState]) -> List[ContactState]:
    return [c for c in contacts if c.valid and c.mu >= 0.0]


def solve_bounded_wrench(
    contacts: Sequence[ContactState],
    w_ext: np.ndarray,
    fn_min: float = 0.0,
    fn_max: float = FN_MAX_DEFAULT,
    total_budget: Optional[float] = None,
    mu_scale: float = 1.0,
    mu_override: Optional[Sequence[float]] = None,
    spread_load: bool = False,
) -> Dict[str, object]:
    """
    Minimum-total-normal-force linear program.

    Variables are (fn_i, ft_i) for active contacts.
    Equality is contact wrench + w_ext = 0.
    """
    active = _active(contacts)
    w_ext = np.asarray(w_ext, dtype=float)
    if len(active) == 0:
        resid = w_ext.copy()
        feasible = bool(np.linalg.norm(resid) <= RESIDUAL_TOL)
        return {
            "feasible": feasible,
            "fn": np.zeros(len(contacts)),
            "ft": np.zeros(len(contacts)),
            "residual": resid,
            "sum_fn": 0.0,
            "reason": "no_active_contacts" if not feasible else "zero_wrench_with_no_contacts",
        }

    n = len(active)
    n_var = 2 * n + (1 if spread_load else 0)
    objective = np.zeros(n_var)
    if spread_load:
        objective[-1] = 1.0
    else:
        objective[0::2] = 1.0
    a_eq = np.zeros((3, n_var))
    mus = []
    for i, contact in enumerate(active):
        mu = float(contact.mu if mu_override is None else mu_override[i]) * float(mu_scale)
        mus.append(mu)
        force_n = contact.normal
        force_t = contact.tangent
        lever_n = contact.position[0] * force_n[1] - contact.position[1] * force_n[0]
        lever_t = contact.position[0] * force_t[1] - contact.position[1] * force_t[0]
        a_eq[0, 2 * i] = force_n[0]
        a_eq[1, 2 * i] = force_n[1]
        a_eq[2, 2 * i] = lever_n
        a_eq[0, 2 * i + 1] = force_t[0]
        a_eq[1, 2 * i + 1] = force_t[1]
        a_eq[2, 2 * i + 1] = lever_t

    a_ub = []
    b_ub = []
    for i, _contact in enumerate(active):
        mu = mus[i]
        row_pos = np.zeros(n_var)
        row_neg = np.zeros(n_var)
        row_pos[2 * i] = -mu
        row_pos[2 * i + 1] = 1.0
        row_neg[2 * i] = -mu
        row_neg[2 * i + 1] = -1.0
        a_ub.append(row_pos)
        a_ub.append(row_neg)
        b_ub.append(0.0)
        b_ub.append(0.0)
    if total_budget is not None:
        row = np.zeros(n_var)
        row[0::2] = 1.0
        a_ub.append(row)
        b_ub.append(float(total_budget))
    if spread_load:
        for i in range(n):
            row = np.zeros(n_var)
            row[2 * i] = 1.0
            row[-1] = -1.0
            a_ub.append(row)
            b_ub.append(0.0)

    bounds = []
    for _ in active:
        bounds.append((float(fn_min), float(fn_max)))
        bounds.append((None, None))
    if spread_load:
        bounds.append((0.0, float(fn_max)))

    result = linprog(
        objective,
        A_ub=np.asarray(a_ub, dtype=float),
        b_ub=np.asarray(b_ub, dtype=float),
        A_eq=a_eq,
        b_eq=-w_ext,
        bounds=bounds,
        method="highs",
    )
    fn_full = np.zeros(len(contacts))
    ft_full = np.zeros(len(contacts))
    if not result.success or result.x is None:
        return {
            "feasible": False,
            "fn": fn_full,
            "ft": ft_full,
            "residual": None,
            "sum_fn": None,
            "reason": "optimizer_infeasible",
        }

    x = np.asarray(result.x, dtype=float)
    id_to_pos = {c.contact_id: i for i, c in enumerate(contacts)}
    for i, contact in enumerate(active):
        pos = id_to_pos[contact.contact_id]
        fn_full[pos] = x[2 * i]
        ft_full[pos] = x[2 * i + 1]
        mu = mus[i]
        if fn_full[pos] < fn_min - CONSTRAINT_TOL or fn_full[pos] > fn_max + CONSTRAINT_TOL:
            return _reject(fn_full, ft_full, "normal_bound")
        if abs(ft_full[pos]) > mu * fn_full[pos] + 1e-5:
            return _reject(fn_full, ft_full, "friction_cone")
    if total_budget is not None and float(np.sum(fn_full)) > float(total_budget) + 1e-5:
        return _reject(fn_full, ft_full, "total_budget")
    resid = equilibrium_residual(contacts, fn_full, ft_full, w_ext)
    if float(np.max(np.abs(resid))) > 1e-4:
        return {
            "feasible": False,
            "fn": fn_full,
            "ft": ft_full,
            "residual": resid,
            "sum_fn": float(np.sum(fn_full)),
            "reason": "residual_above_tolerance",
        }
    return {
        "feasible": True,
        "fn": fn_full,
        "ft": ft_full,
        "residual": resid,
        "sum_fn": float(np.sum(fn_full)),
        "reason": "verified_equilibrium",
    }


def _reject(fn: np.ndarray, ft: np.ndarray, reason: str) -> Dict[str, object]:
    return {
        "feasible": False,
        "fn": fn,
        "ft": ft,
        "residual": None,
        "sum_fn": float(np.sum(fn)),
        "reason": reason,
    }


def maximum_upward_support(
    contacts: Sequence[ContactState],
    w_ext: np.ndarray,
    fn_max: float = FN_MAX_DEFAULT,
    total_budget: Optional[float] = None,
    mu_override: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """Normal forces that minimize the unbalanced downward load. Not an equilibrium claim."""
    active = _active(contacts)
    fn_full = np.zeros(len(contacts))
    if not active:
        return fn_full
    w_ext = np.asarray(w_ext, dtype=float)
    n = len(active)
    # variables fn, ft, and downward slack s such that contact_fy + w_ext_y + s = 0, s>=0
    # minimizing s maximizes the upward contact force without requiring a full balance.
    objective = np.zeros(2 * n + 1)
    objective[-1] = 1.0
    a_eq = np.zeros((1, 2 * n + 1))
    a_eq[0, -1] = 1.0
    b_eq = np.array([-w_ext[1]])
    a_ub = []
    b_ub = []
    for i, contact in enumerate(active):
        mu = float(contact.mu if mu_override is None else mu_override[i])
        a_eq[0, 2 * i] = contact.normal[1]
        a_eq[0, 2 * i + 1] = contact.tangent[1]
        row_pos = np.zeros(2 * n + 1)
        row_neg = np.zeros(2 * n + 1)
        row_pos[2 * i] = -mu
        row_pos[2 * i + 1] = 1.0
        row_neg[2 * i] = -mu
        row_neg[2 * i + 1] = -1.0
        a_ub.extend([row_pos, row_neg])
        b_ub.extend([0.0, 0.0])
    if total_budget is not None:
        row = np.zeros(2 * n + 1)
        row[0::2][:n] = 1.0
        a_ub.append(row)
        b_ub.append(float(total_budget))
    bounds = []
    for _ in active:
        bounds.append((0.0, float(fn_max)))
        bounds.append((None, None))
    bounds.append((0.0, None))
    result = linprog(objective, A_ub=np.asarray(a_ub), b_ub=np.asarray(b_ub), A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not result.success or result.x is None:
        return fn_full
    id_to_pos = {c.contact_id: i for i, c in enumerate(contacts)}
    for i, contact in enumerate(active):
        fn_full[id_to_pos[contact.contact_id]] = max(0.0, float(result.x[2 * i]))
    return fn_full


def reaction_friction(
    contacts: Sequence[ContactState],
    fn: Sequence[float],
    w_ext: np.ndarray,
) -> Dict[str, object]:
    """
    Tangential reactions at fixed normal force.
    An L1 slack program finds the friction inside the cones closest to balance.
    """
    w_ext = np.asarray(w_ext, dtype=float)
    fn = [float(v) for v in fn]
    active = [(i, c) for i, c in enumerate(contacts) if c.valid and fn[i] > 1e-8]
    ft = np.zeros(len(contacts))
    if not active:
        return {"ft": ft, "residual": w_ext.copy(), "balanced": bool(np.max(np.abs(w_ext)) < 1e-4)}
    n = len(active)
    # variables: ft (n), slack_pos (3), slack_neg (3)
    objective = np.zeros(n + 6)
    objective[n:] = 1.0
    a_eq = np.zeros((3, n + 6))
    b_eq = -w_ext.copy()
    for col, (index, contact) in enumerate(active):
        force = fn[index] * contact.normal
        tau = contact.position[0] * force[1] - contact.position[1] * force[0]
        b_eq[0] -= force[0]
        b_eq[1] -= force[1]
        b_eq[2] -= tau
        lever = contact.position[0] * contact.tangent[1] - contact.position[1] * contact.tangent[0]
        a_eq[0, col] = contact.tangent[0]
        a_eq[1, col] = contact.tangent[1]
        a_eq[2, col] = lever
    for axis in range(3):
        a_eq[axis, n + axis] = -1.0
        a_eq[axis, n + 3 + axis] = 1.0
    bounds = []
    for index, contact in active:
        limit = abs(contact.mu) * fn[index]
        bounds.append((-limit, limit))
    for _ in range(6):
        bounds.append((0.0, None))
    result = linprog(objective, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not result.success or result.x is None:
        return {"ft": ft, "residual": w_ext.copy(), "balanced": False}
    for col, (index, _contact) in enumerate(active):
        ft[index] = float(result.x[col])
    resid = equilibrium_residual(contacts, fn, ft, w_ext)
    balanced = float(np.max(np.abs(resid))) < 1e-4
    return {"ft": ft, "residual": resid, "balanced": balanced}


def geometric_force_closure(contacts: Sequence[ContactState], magnitude: float = 1.0) -> Dict[str, object]:
    """Both signs of each wrench basis direction must be realizable with a large normal-force cap."""
    directions = []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            w = np.zeros(3)
            w[axis] = sign * magnitude
            directions.append(w)
    failures = []
    for w in directions:
        solved = solve_bounded_wrench(contacts, w, fn_min=0.0, fn_max=1.0e5, total_budget=None)
        if not solved["feasible"]:
            failures.append(w.tolist())
    return {
        "geometric_force_closure": len(failures) == 0,
        "failed_directions": failures,
        "directions_tested": len(directions),
    }


def robust_feasible(
    contacts: Sequence[ContactState],
    w_ext: np.ndarray,
    sigma: Sequence[float],
    k: float,
    fn_max: float = FN_MAX_DEFAULT,
    total_budget: Optional[float] = None,
) -> Dict[str, object]:
    active = _active(contacts)
    override = []
    sig = list(sigma)
    for i, contact in enumerate(active):
        override.append(max(0.02, float(contact.mu) - k * float(sig[i])))
    solved = solve_bounded_wrench(
        contacts,
        w_ext,
        fn_max=fn_max,
        total_budget=total_budget,
        mu_override=override,
    )
    solved["robust"] = bool(solved["feasible"])
    return solved


def dynamic_recoverability(
    static_feasible: bool,
    fn_now: Sequence[float],
    fn_target: Sequence[float],
    speed: float,
    distance_to_boundary_m: float = 0.025,
    slew_n_per_s: float = 50.0,
    conservative_accel: float = 2.0,
) -> Dict[str, object]:
    if not static_feasible:
        return {"dynamically_recoverable": False, "reason": "no_static_solution", "ramp_time_s": None, "time_to_boundary_s": None}
    deltas = [max(0.0, float(b) - float(a)) for a, b in zip(fn_now, fn_target)]
    ramp = max(deltas) / slew_n_per_s if deltas else 0.0
    # Quadratic under a constant outward acceleration, starting from the current speed.
    # 0.5 a t^2 + v t - d = 0.
    a = max(conservative_accel, 1e-6)
    disc = speed * speed + 2.0 * a * max(distance_to_boundary_m, 0.0)
    t_bound = (-speed + math_sqrt(disc)) / a if False else (-float(speed) + float(np.sqrt(disc))) / a
    t_bound = max(0.0, t_bound)
    return {
        "dynamically_recoverable": bool(ramp <= t_bound + 1e-9),
        "reason": "ramp_within_boundary_time" if ramp <= t_bound + 1e-9 else "slew_too_slow",
        "ramp_time_s": float(ramp),
        "time_to_boundary_s": float(t_bound),
    }


def math_sqrt(value: float) -> float:
    return float(np.sqrt(value))


def classify_five(
    contacts: Sequence[ContactState],
    w_ext: np.ndarray,
    fn_now: Sequence[float],
    sigma: Sequence[float],
    total_budget: Optional[float],
    speed: float,
    k_robust: float = 1.0,
) -> Dict[str, object]:
    closure = geometric_force_closure(contacts)
    wrench = solve_bounded_wrench(contacts, w_ext, fn_max=1.0e5, total_budget=None)
    bounded = solve_bounded_wrench(contacts, w_ext, fn_max=FN_MAX_DEFAULT, total_budget=total_budget)
    robust = robust_feasible(contacts, w_ext, sigma, k_robust, total_budget=total_budget)
    dynamic = dynamic_recoverability(
        bool(bounded["feasible"]),
        fn_now,
        bounded["fn"] if bounded["fn"] is not None else fn_now,
        speed,
    )
    return {
        "geometric_force_closure": closure["geometric_force_closure"],
        "wrench_feasibility": bool(wrench["feasible"]),
        "bounded_actuator_feasibility": bool(bounded["feasible"]),
        "robust_feasibility": bool(robust["feasible"]),
        "dynamic_recoverability": bool(dynamic["dynamically_recoverable"]),
        "bounded_solution": bounded,
        "dynamic_detail": dynamic,
        "closure_detail": closure,
    }


def two_contact_mu_crit_case() -> Dict[str, object]:
    """Reproduce the P5-T3 symmetric pair with the verified LP, not the prose claim."""
    from contact_wrench_analysis import make_radial_contact

    com = np.zeros(2)
    contacts = [
        make_radial_contact(0, 0.0, 0.040, 0.55, 4.0e4, com),
        make_radial_contact(1, 2.0 * np.pi / 3.0, 0.040, 0.53, 4.0e4, com),
        make_radial_contact(2, 4.0 * np.pi / 3.0, 0.040, 0.56, 4.0e4, com),
    ]
    contacts[0].valid = False
    w_ext = np.zeros(3)
    bounded = solve_bounded_wrench(contacts, w_ext, fn_min=3.0, fn_max=25.0)
    bounded_nonneg = solve_bounded_wrench(contacts, w_ext, fn_min=0.0, fn_max=25.0)
    gravity = solve_bounded_wrench(contacts, np.array([0.0, -0.5 * 9.81, 0.0]), fn_min=0.0, fn_max=25.0)
    margin = symmetric_pair_horizontal_margin(0.53, 0.56, 8.0, 8.0)
    return {
        "horizontal_margin_at_8N": margin,
        "bounded_with_fn_at_least_3N": bounded,
        "bounded_with_fn_nonnegative_and_zero_external": bounded_nonneg,
        "bounded_under_0.5kg_gravity": gravity,
    }
