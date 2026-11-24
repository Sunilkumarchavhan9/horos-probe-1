"""
HOROS PROBE-1 — Phase 5 Task 4R4
Adaptive Preload Planner
File: adaptive_preload_planner.py

Observes initial micro-probe contact telemetry, estimates friction and uncertainty,
and plans the minimum safe initial normal preload.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Any, Sequence
import numpy as np

import _paths  # noqa: F401
from friction_probe_estimator import FrictionProbeEstimator
from friction_uncertainty_model import FrictionUncertaintyModel
from preload_force_allocator import PreloadForceAllocator


class AdaptivePreloadPlanner:
    """
    Two-phase adaptive planner:
      Phase 1 (t <= 30 ms): Controlled micro-probing at 4.0 N to observe partial-slip softening.
      Phase 2 (t > 30 ms): Estimate mu, evaluate uncertainty, and lock in minimum sufficient preload.
    """
    name = "AdaptivePreloadPlanner"

    def __init__(
        self,
        num_contacts: int,
        probe_duration_steps: int = 30,
        probe_fn: float = 4.0,
        safety_margin_N: float = 1.5,
        slew_limit: float = 50.0,
        dt: float = 0.001,
        seed: int = 4930001,
    ):
        self.num_contacts = int(num_contacts)
        self.probe_duration = int(probe_duration_steps)
        self.probe_fn = float(probe_fn)
        self.dt = float(dt)

        self.estimator = FrictionProbeEstimator(
            probe_duration_steps=probe_duration_steps,
            probe_fn_target=probe_fn,
        )
        self.uncertainty_model = FrictionUncertaintyModel(safety_margin_N=safety_margin_N)
        self.allocator = PreloadForceAllocator(slew_limit=slew_limit, dt=dt)

        self.reset()

    def reset(self):
        self.step_count = 0
        self.phase = "PROBING"
        self.fn_history: Dict[int, List[float]] = {i: [] for i in range(self.num_contacts)}
        self.ft_history: Dict[int, List[float]] = {i: [] for i in range(self.num_contacts)}
        self.disp_history: Dict[int, List[float]] = {i: [] for i in range(self.num_contacts)}

        self.mu_hats = [0.55] * self.num_contacts
        self.mu_sigmas = [0.20] * self.num_contacts
        self.confidences = [0.20] * self.num_contacts
        self.selected_target_fn = self.probe_fn
        self.planned = False
        self.planning_info: Dict[str, Any] = {}
        self.allocator.reset([self.probe_fn] * self.num_contacts)

    def step(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Advances planner:
        - During probe phase: commands safe probe load.
        - At transition: fits friction and selects preload.
        - Post-probe: applies and maintains allocated preload.
        """
        self.step_count += 1
        valid = [bool(v) for v in obs.get("valid", [True] * self.num_contacts)]
        current_fn = [float(f) for f in obs.get("fn", [self.probe_fn] * self.num_contacts)]
        normals = obs.get("normals", [[0.0, 1.0]] * self.num_contacts)
        positions = obs.get("positions", [[0.0, 0.0]] * self.num_contacts)
        w_ext_hat = obs.get("w_ext_hat", [0.0, 0.0, 0.0])
        budget = obs.get("protocol_budget")

        # Record probe telemetry
        for i in range(self.num_contacts):
            if valid[i]:
                self.fn_history[i].append(current_fn[i])
                ft_val = float(obs.get("ft", [0.0] * self.num_contacts)[i])
                self.ft_history[i].append(ft_val)
                disp_val = float(obs.get("disp", [0.0] * self.num_contacts)[i])
                self.disp_history[i].append(disp_val)

        # Transition: Estimation & Planning
        if self.step_count == self.probe_duration and not self.planned:
            self.phase = "PRELOAD_LATCHED"
            self.planned = True

            # Fit friction per contact
            estimated_mu_list = []
            estimated_sigma_list = []
            confidence_list = []
            for i in range(self.num_contacts):
                res = self.estimator.estimate_from_probe(
                    self.fn_history[i],
                    self.ft_history[i],
                    self.disp_history[i],
                )
                self.mu_hats[i] = res["mu_hat"]
                self.mu_sigmas[i] = res["mu_sigma"]
                self.confidences[i] = res["confidence"]
                estimated_mu_list.append(res["mu_hat"])
                estimated_sigma_list.append(res["mu_sigma"])
                confidence_list.append(res["confidence"])

            # Compute conservative friction bound across active contacts
            min_mu_safe = 1.0
            avg_conf = float(np.mean(confidence_list))
            for i in range(self.num_contacts):
                bound = self.uncertainty_model.compute_safe_friction_bound(
                    self.mu_hats[i],
                    self.mu_sigmas[i],
                    self.confidences[i],
                )
                if bound["mu_safe"] < min_mu_safe:
                    min_mu_safe = bound["mu_safe"]

            # Estimate anticipated shear load from early wrench trend or nominal shear
            f_ext_mag = float(np.linalg.norm(w_ext_hat[:2]))
            f_shear_anticipated = max(3.0, f_ext_mag)

            plan_res = self.uncertainty_model.compute_required_preload(
                mu_safe=min_mu_safe,
                f_shear_anticipated=f_shear_anticipated,
                num_contacts=sum(1 for v in valid if v),
                confidence=avg_conf,
            )
            self.selected_target_fn = plan_res["fn_target"]
            self.planning_info = {
                "mu_hats": estimated_mu_list,
                "mu_sigmas": estimated_sigma_list,
                "confidences": confidence_list,
                "min_mu_safe": min_mu_safe,
                "f_shear_anticipated": f_shear_anticipated,
                "selected_preload": self.selected_target_fn,
                "strategy": plan_res["strategy"],
                "reason": plan_res["reason"],
            }

        # Select target normal force
        if self.phase == "PROBING":
            active_target = self.probe_fn
        else:
            active_target = self.selected_target_fn

        cmd = self.allocator.allocate(
            current_fn=current_fn,
            target_fn_per_contact=active_target,
            normals=normals,
            positions=positions,
            valid=valid,
            w_ext_hat=w_ext_hat,
            budget=budget,
        )

        return {
            "cmd_fn": cmd.tolist(),
            "phase": self.phase,
            "selected_preload": self.selected_target_fn,
            "mu_hats": list(self.mu_hats),
            "planning_info": self.planning_info,
        }
