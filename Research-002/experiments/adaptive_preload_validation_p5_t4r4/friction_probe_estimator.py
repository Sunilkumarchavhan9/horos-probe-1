"""
HOROS PROBE-1 — Phase 5 Task 4R4
Causal Pre-Contact Friction Probe Estimator
File: friction_probe_estimator.py

Estimates friction coefficient mu and tangential stiffness kt from a low-energy
active micro-probe trajectory during contact formation.
Operates strictly on noisy observable telemetry:
- Contact normal force Fn_meas (with noise sigma_F = 20 mN)
- Contact tangential force Ft_meas (with noise sigma_F = 20 mN)
- Contact displacement delta_meas (with noise sigma_delta = 0.5 um)

Physical Identifiability Constraint:
If tangential force Ft remains well below the friction cone (|Ft| / Fn < 0.30),
the contact remains inside the linear sticking core. In this regime, mu is
physically unidentifiable from slope alone. The estimator explicitly reports
high uncertainty and low confidence in this unidentifiable regime.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple
import numpy as np


class FrictionProbeEstimator:
    """
    Estimates contact friction coefficient and tangential compliance from
    a controlled micro-probe sequence.
    """

    def __init__(
        self,
        probe_duration_steps: int = 30,     # 30 ms probing window at 1000 Hz
        probe_fn_target: float = 4.0,       # 4.0 N safe contact load
        fingertip_radius: float = 0.015,    # 15 mm nominal fingertip radius
        e_star_nominal: float = 1.589e6,    # 1.589 MPa silicone-aluminum E*
        g_star_nominal: float = 2.667e5,    # 0.267 MPa silicone-aluminum G*
    ):
        self.probe_duration_steps = int(probe_duration_steps)
        self.probe_fn_target = float(probe_fn_target)
        self.R = float(fingertip_radius)
        self.E_star = float(e_star_nominal)
        self.G_star = float(g_star_nominal)

    def estimate_from_probe(
        self,
        fn_history: List[float],
        ft_history: List[float],
        disp_history: List[float],
    ) -> Dict[str, float]:
        """
        Fits Cattaneo-Mindlin partial slip response to noisy micro-probe telemetry.
        Respects identifiability boundary.
        """
        if len(fn_history) < 10 or len(ft_history) < 10:
            return {
                "mu_hat": 0.55,
                "mu_sigma": 0.25,
                "kt_hat": 5000.0,
                "slip_onset_disp": 0.0001,
                "confidence": 0.20,
                "identifiable": 0.0,
            }

        fn_arr = np.asarray(fn_history, dtype=float)
        ft_arr = np.abs(np.asarray(ft_history, dtype=float))
        disp_arr = np.abs(np.asarray(disp_history, dtype=float))

        fn_mean = max(1.0, float(np.mean(fn_arr)))
        max_ft = float(np.max(ft_arr))
        max_ratio = max_ft / fn_mean

        # Theoretical initial contact radius and stiffness
        a0 = (3.0 * fn_mean * self.R / (4.0 * self.E_star)) ** (1.0 / 3.0)
        kt0_theory = 8.0 * self.G_star * a0

        # Empirical initial tangential stiffness from first 8-12 steps (elastic core)
        valid_idx = np.where(disp_arr > 1e-6)[0]
        if len(valid_idx) >= 5:
            early_idx = valid_idx[:min(10, len(valid_idx))]
            disp_sub = disp_arr[early_idx]
            ft_sub = ft_arr[early_idx]
            if np.max(disp_sub) - np.min(disp_sub) > 1e-7:
                slope, _ = np.polyfit(disp_sub, ft_sub, 1)
                kt_hat = float(np.clip(slope, 0.5 * kt0_theory, 2.0 * kt0_theory))
            else:
                kt_hat = float(kt0_theory)
        else:
            kt_hat = float(kt0_theory)

        # Check physical identifiability
        # If max_ratio < 0.25, contact is deep in sticking regime -> mu unidentifiable
        is_identifiable = max_ratio >= 0.25

        # Fit Cattaneo-Mindlin model
        mu_candidates = np.linspace(0.20, 1.00, 81)
        best_mu = 0.55
        min_residual = float("inf")
        residuals_list = []

        for mu_c in mu_candidates:
            arg = np.clip(1.0 - (kt_hat * disp_arr) / max(1e-6, 3.0 * mu_c * fn_mean), 0.0, 1.0)
            ft_pred = mu_c * fn_mean * (1.0 - (arg ** 3))
            res = float(np.sum((ft_arr - ft_pred) ** 2))
            residuals_list.append(res)
            if res < min_residual:
                min_residual = res
                best_mu = float(mu_c)

        # In unidentifiable regime, fallback to uninformative prior with high sigma
        if not is_identifiable:
            mu_sigma = 0.22
            confidence = float(np.clip(max_ratio / 0.35, 0.15, 0.40))
            best_mu = max(best_mu, 0.55)
        else:
            best_idx = int(np.argmin(residuals_list))
            d_mu = mu_candidates[1] - mu_candidates[0]
            if 1 <= best_idx < len(residuals_list) - 1:
                second_deriv = (residuals_list[best_idx + 1] - 2.0 * residuals_list[best_idx] + residuals_list[best_idx - 1]) / (d_mu ** 2)
                n_pts = len(ft_arr)
                sigma_noise_sq = max(1e-6, min_residual / max(1, n_pts - 2))
                if second_deriv > 1e-6:
                    mu_variance = 2.0 * sigma_noise_sq / second_deriv
                    mu_sigma = float(np.clip(math.sqrt(max(1e-6, mu_variance)), 0.04, 0.18))
                else:
                    mu_sigma = 0.14
            else:
                mu_sigma = 0.15
            fit_r2 = max(0.10, 1.0 - (min_residual / max(1e-6, float(np.sum((ft_arr - np.mean(ft_arr)) ** 2)))))
            confidence = float(np.clip(fit_r2 * (1.0 - mu_sigma), 0.35, 0.95))

        slip_onset_disp = 3.0 * best_mu * fn_mean * (1.0 - math.sqrt(0.80)) / max(1.0, kt_hat)

        return {
            "mu_hat": best_mu,
            "mu_sigma": mu_sigma,
            "kt_hat": kt_hat,
            "slip_onset_disp": float(slip_onset_disp),
            "confidence": confidence,
            "identifiable": 1.0 if is_identifiable else 0.0,
        }
