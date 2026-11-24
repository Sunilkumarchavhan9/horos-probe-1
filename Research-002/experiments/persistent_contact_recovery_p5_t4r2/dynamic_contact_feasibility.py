"""Five feasibility answers. They are not synonyms."""

from __future__ import annotations

import _paths  # noqa: F401
from typing import Dict, Sequence

import numpy as np

from contact_feasibility_solver import solve_bounded_wrench
from contact_wrench_analysis import ContactState
from p5_t4r2_experiment_protocol import FN_MAX, SLEW


def classify(contacts: Sequence[ContactState], fn_now, w_ext, speed: float, disp: float, recontact_possible: bool, budget):
    wrench = np.asarray(w_ext, dtype=float)
    static = solve_bounded_wrench(contacts, wrench, fn_min=0.0, fn_max=FN_MAX, total_budget=None)
    bounded = solve_bounded_wrench(contacts, wrench, fn_min=0.0, fn_max=FN_MAX, total_budget=budget)
    static_ok = bool(static["feasible"])
    bounded_ok = bool(bounded["feasible"])
    target = np.asarray(bounded["fn"] if bounded_ok else static["fn"], dtype=float)
    now = np.asarray(fn_now, dtype=float)
    gap = 0.0
    for index, contact in enumerate(contacts):
        if contact.valid:
            gap = max(gap, abs(float(target[index]) - float(now[index])))
    time_left = 0.35 if speed <= 1e-4 else max(0.0, (0.025 - float(disp)) / float(speed))
    rate_ok = bool(static_ok and gap <= SLEW * time_left + 1e-6)
    robust = solve_bounded_wrench(
        contacts, np.asarray(w_ext, dtype=float) * 1.1, fn_min=0.0, fn_max=FN_MAX,
        total_budget=budget, mu_scale=0.75,
    )
    return {
        "static_wrench_feasibility": static_ok,
        "bounded_force_feasibility": bounded_ok,
        "rate_limited_dynamic_feasibility": rate_ok,
        "recontact_feasibility": bool(recontact_possible),
        "robust_dynamic_recovery_feasibility": bool(robust["feasible"] and rate_ok and recontact_possible),
        "slew_gap_n": gap,
        "time_left_s": time_left,
    }
