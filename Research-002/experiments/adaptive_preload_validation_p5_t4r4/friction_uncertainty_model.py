"""
HOROS PROBE-1 — Phase 5 Task 4R4
Friction Uncertainty Model & Preload Safety Calibration
File: friction_uncertainty_model.py

Transforms point friction estimates and uncertainty into conservative,
statistically calibrated contact margins.
"""

from __future__ import annotations

import math
from typing import Dict, Any, Optional
import numpy as np


class FrictionUncertaintyModel:
    """
    Computes uncertainty-aware lower bounds on friction and required normal preload.
    """

    def __init__(
        self,
        coverage_z: float = 1.96,       # 95% one-sided conservative coverage
        min_friction_floor: float = 0.15, # Minimum plausible friction floor
        safety_margin_N: float = 1.5,    # 1.5 N buffer above critical slip boundary
        low_confidence_threshold: float = 0.35, # Fallback trigger
        conservative_fallback_N: float = 16.0,   # Safe default if estimation fails
    ):
        self.z = float(coverage_z)
        self.min_floor = float(min_friction_floor)
        self.safety_margin = float(safety_margin_N)
        self.low_conf_thresh = float(low_confidence_threshold)
        self.fallback_fn = float(conservative_fallback_N)

    def compute_safe_friction_bound(
        self,
        mu_hat: float,
        mu_sigma: float,
        confidence: float,
    ) -> Dict[str, float]:
        """
        Computes conservative lower bound on friction:
            mu_safe = max(floor, mu_hat - z * mu_sigma / sqrt(confidence))
        """
        effective_sigma = float(mu_sigma) / max(0.20, math.sqrt(float(confidence)))
        lower_bound = float(mu_hat) - self.z * effective_sigma
        mu_safe = float(np.clip(lower_bound, self.min_floor, float(mu_hat)))

        return {
            "mu_hat": float(mu_hat),
            "mu_sigma": float(mu_sigma),
            "effective_sigma": effective_sigma,
            "mu_safe": mu_safe,
            "uncertainty_spread": float(mu_hat) - mu_safe,
        }

    def compute_required_preload(
        self,
        mu_safe: float,
        f_shear_anticipated: float,
        num_contacts: int,
        confidence: float,
        fn_min: float = 3.0,
        fn_max: float = 25.0,
    ) -> Dict[str, Any]:
        """
        Calculates minimum sufficient normal preload per contact:
            Fn_req = |F_shear| / (N_contacts * mu_safe) + safety_margin
        If confidence is below threshold, triggers conservative fallback.
        """
        is_fallback = float(confidence) < self.low_conf_thresh
        f_shear = abs(float(f_shear_anticipated))
        n_c = max(1, int(num_contacts))

        if is_fallback:
            fn_target = float(np.clip(self.fallback_fn, fn_min, fn_max))
            strategy = "fallback_conservative"
            reason = f"low_estimation_confidence_{confidence:.2f}"
        else:
            # Physics-based minimum normal force per contact
            fn_nominal_needed = (f_shear / (n_c * max(0.10, mu_safe))) + self.safety_margin
            fn_target = float(np.clip(fn_nominal_needed, fn_min, fn_max))
            strategy = "calibrated_uncertainty"
            reason = "sufficient_confidence_probe"

        return {
            "fn_target": fn_target,
            "f_shear_anticipated": f_shear,
            "mu_safe_used": float(mu_safe),
            "is_fallback": is_fallback,
            "strategy": strategy,
            "reason": reason,
        }
