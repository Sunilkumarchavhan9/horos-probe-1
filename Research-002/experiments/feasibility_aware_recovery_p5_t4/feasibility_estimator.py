"""
Causal feasibility estimator.

Monte Carlo friction samples are drawn from the controller's own estimate and
its stated uncertainty. True friction is never an input.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from contact_feasibility_solver import solve_bounded_wrench
from contact_wrench_analysis import ContactState
from feasibility_uncertainty import wilson_interval


class CausalFeasibilityEstimator:
    def __init__(
        self,
        p_low: float = 0.35,
        p_high: float = 0.80,
        n_samples: int = 8,
        seed: int = 4810001,
    ):
        self.p_low = float(p_low)
        self.p_high = float(p_high)
        self.n_samples = int(n_samples)
        self.rng = np.random.RandomState(seed)
        self.samples_used = 0

    def reset(self, seed: Optional[int] = None):
        if seed is not None:
            self.rng = np.random.RandomState(int(seed) + 17)

    def estimate(
        self,
        contacts: Sequence[ContactState],
        mu_hat: Sequence[float],
        mu_sigma: Sequence[float],
        w_ext_hat: np.ndarray,
        speed: float,
        total_budget: Optional[float],
    ) -> Dict[str, object]:
        active_ids = [c.contact_id for c in contacts if c.valid]
        if not active_ids:
            return self._pack(0.0, 0, "INFEASIBLE", ["no_active_contacts"], speed, None)

        successes = 0
        reasons: List[str] = []
        last = None
        for sample_index in range(self.n_samples):
            override = []
            for contact in contacts:
                if not contact.valid:
                    continue
                sigma = max(0.01, float(mu_sigma[contact.contact_id]))
                draw = float(self.rng.normal(float(mu_hat[contact.contact_id]), sigma))
                override.append(float(np.clip(draw, 0.02, 1.4)))
            self.samples_used += 1
            # Half of the samples use a downward shift so the probability is conservative.
            if sample_index % 2 == 0:
                override = [max(0.02, m - 0.5 * float(mu_sigma[active_ids[j]])) for j, m in enumerate(override)]
            solved = solve_bounded_wrench(
                contacts,
                np.asarray(w_ext_hat, dtype=float),
                fn_min=0.0,
                fn_max=25.0,
                total_budget=total_budget,
                mu_override=override,
            )
            last = solved
            successes += int(bool(solved["feasible"]))
        p = successes / float(self.n_samples)
        if p >= self.p_high:
            label = "FEASIBLE"
            reasons.append("lower_tail_samples_still_balance")
        elif p <= self.p_low:
            label = "INFEASIBLE"
            reasons.append("friction_samples_have_no_verified_balance")
        else:
            label = "UNCERTAIN"
            reasons.append("sample_fraction_inside_uncertainty_band")
        if label == "FEASIBLE" and speed > 0.08:
            label = "DEGRADED_RECOVERY"
            reasons.append("speed_high_relative_to_slew")
        if last is not None and last.get("reason") == "residual_above_tolerance":
            reasons.append("a_sample_failed_residual_check")
        nominal = solve_bounded_wrench(
            contacts,
            np.asarray(w_ext_hat, dtype=float),
            total_budget=total_budget,
            mu_override=[max(0.02, float(mu_hat[c.contact_id])) for c in contacts if c.valid],
        )
        recovery_p = float(p if label in ("FEASIBLE", "DEGRADED_RECOVERY") else 0.15 * p)
        time_to_instability = None
        if speed > 1e-4:
            time_to_instability = max(0.0, 0.025 / float(speed))
        elif label == "INFEASIBLE":
            time_to_instability = 0.35
        return self._pack(p, successes, label, reasons, speed, nominal, recovery_p, time_to_instability)

    def _pack(self, p, successes, label, reasons, speed, nominal, recovery_p=0.0, t_inst=None):
        return {
            "probability": float(p),
            "probability_interval": wilson_interval(int(successes) if p > 0 or successes == 0 else 0, self.n_samples),
            "label": label,
            "recovery_probability": float(recovery_p),
            "time_to_instability_s": t_inst,
            "reasons": reasons,
            "nominal_solution": nominal,
            "speed": float(speed),
        }
