"""Force allocation by a verified linear program on estimated parameters."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from contact_feasibility_solver import solve_bounded_wrench
from contact_wrench_analysis import ContactState


def allocate_normal_forces(
    contacts: Sequence[ContactState],
    mu_hat: Sequence[float],
    w_ext_hat: np.ndarray,
    total_budget: Optional[float],
    residual_mode: str = "none",
    fn_max: float = 25.0,
) -> np.ndarray:
    override = [max(0.02, float(mu_hat[c.contact_id])) for c in contacts if c.valid]
    solved = solve_bounded_wrench(
        contacts,
        np.asarray(w_ext_hat, dtype=float),
        fn_min=0.0,
        fn_max=float(fn_max),
        total_budget=total_budget,
        mu_override=override,
        spread_load=True,
    )
    target = np.zeros(len(contacts))
    if solved["feasible"]:
        target = np.asarray(solved["fn"], dtype=float).copy()
    else:
        # No verified target. Leave zeros so the caller can degrade instead of squeezing.
        return target

    if residual_mode == "boost_best_mu":
        valid = [c.contact_id for c in contacts if c.valid]
        if valid:
            best = max(valid, key=lambda i: float(mu_hat[i]))
            target[best] = min(float(fn_max), target[best] + 1.0)
    elif residual_mode == "oppose_load":
        force = np.asarray(w_ext_hat[:2], dtype=float)
        if float(np.linalg.norm(force)) > 1e-6:
            direction = force / np.linalg.norm(force)
            scores = []
            for c in contacts:
                if c.valid:
                    scores.append((float(np.dot(c.normal, -direction)), c.contact_id))
            if scores:
                scores.sort(reverse=True)
                target[scores[0][1]] = min(float(fn_max), target[scores[0][1]] + 1.0)
                if len(scores) > 1:
                    target[scores[-1][1]] = max(0.0, target[scores[-1][1]] - 0.5)

    if total_budget is not None and float(np.sum(target)) > float(total_budget):
        scale = float(total_budget) / max(1e-9, float(np.sum(target)))
        target *= scale
    return np.clip(target, 0.0, float(fn_max))
