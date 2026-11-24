"""Predictive recovery. Classification stays causal. Force commands are rate-limited by the plant."""

from __future__ import annotations

import _paths  # noqa: F401
import time
from typing import Dict, List, Optional

import numpy as np

from contact_wrench_analysis import ContactState
from deadline_aware_solver import solve_with_deadline
from dynamic_feasibility_solver import classify_dynamic
from feasibility_estimator import CausalFeasibilityEstimator
from p5_t4_revision_state_machine import DEGRADE, EVALUATE, RECOVERY, TERMINAL, VERIFIED, RevisionStateMachine
from safe_degradation_controller import SafeDegradationController
from contact_reestablishment_planner import predict_contacts


def _contacts(obs: Dict[str, object]) -> List[ContactState]:
    built = []
    for i in range(int(obs["num_contacts"])):
        built.append(ContactState(
            contact_id=i,
            position=np.asarray(obs["position"][i], dtype=float),
            normal=np.asarray(obs["normal"][i], dtype=float),
            tangent=np.asarray(obs["tangent"][i], dtype=float),
            mu=float(obs["mu_hat"][i]),
            fn=float(obs["fn_applied"][i]),
            valid=bool(obs["valid"][i]),
            mu_sigma=float(obs["mu_sigma"][i]),
        ))
    return built


class PredictiveRecoveryController:
    name = "P5T4CorrectiveRecovery"

    def __init__(
        self,
        num_contacts: int,
        mode: str = "min_surviving",
        p_low: float = 0.20,
        p_high: float = 0.65,
        seed: int = 1,
        use_recontact: bool = True,
        degrade_if_infeasible: bool = True,
        use_predictor: bool = True,
    ):
        self.num_contacts = int(num_contacts)
        requested = str(mode)
        self.use_preload = requested.startswith("preload_")
        self.preload = float(requested.split("_")[1]) if self.use_preload else 16.0
        self.mode = requested if use_predictor or self.use_preload else "fixed_margin"
        self.use_recontact = bool(use_recontact)
        self.degrade_if_infeasible = bool(degrade_if_infeasible)
        self.machine = RevisionStateMachine()
        self.estimator = CausalFeasibilityEstimator(p_low=p_low, p_high=p_high, seed=seed)
        self.safe = SafeDegradationController(num_contacts)
        self.reset_state()

    def reset(self, obs: Optional[Dict[str, object]] = None):
        self.machine.reset()
        self.estimator.reset(None if obs is None else int(float(obs.get("time", 0.0)) * 1000) + self.num_contacts)
        self.safe.reset()
        self.reset_state()
        if obs is not None:
            self.prev_valid = [bool(v) for v in obs["valid"]]
            self.hold = np.asarray(obs["fn_applied"], dtype=float).copy()

    def reset_state(self):
        self.prev_valid = [True] * self.num_contacts
        self.hold = np.zeros(self.num_contacts)
        self.target: Optional[np.ndarray] = None
        self.chosen_total = 0.0
        self.decided = False
        self.estimate = None
        self.decision_label: Optional[str] = None
        self.dynamic_report = None
        self.recontact_plan: List[Dict[str, object]] = []
        self.degrade_cmd: Optional[np.ndarray] = None
        self.solver_ms = 0.0
        self.record_dynamic = False

    def step(self, obs: Dict[str, object]) -> Dict[str, object]:
        started = time.perf_counter()
        valid = [bool(v) for v in obs["valid"]]
        lost_now = any(prev and not now for prev, now in zip(self.prev_valid, valid))
        speed_now = float(np.linalg.norm(obs["object_velocity"]))
        if speed_now > 0.06 and not self.decided:
            lost_now = True
        warning = False
        for i, is_valid in enumerate(valid):
            if not is_valid:
                continue
            limit = abs(float(obs["mu_hat"][i])) * max(float(obs["fn"][i]), 0.1)
            if abs(float(obs["ft"][i])) > 0.85 * limit and limit > 0.2:
                warning = True
        n_active = sum(1 for v in valid if v)
        budget = None if obs.get("protocol_budget") is None else float(obs["protocol_budget"]) * max(n_active, 1)
        if self.machine.state == EVALUATE and not self.decided:
            contacts = _contacts(obs)
            self.estimate = self.estimator.estimate(
                contacts, obs["mu_hat"], obs["mu_sigma"],
                np.asarray(obs["external_wrench_hat"], dtype=float), speed_now, budget,
            )
            self.decided = True
            self.decision_label = str(self.estimate["label"])
            nominal = self.estimate.get("nominal_solution") or {}
            static_fn = nominal.get("fn")
            attempt = self.decision_label in ("FEASIBLE", "DEGRADED_RECOVERY") or not self.degrade_if_infeasible
            if self.use_preload:
                self.target = np.full(self.num_contacts, self.preload)
                self.chosen_total = self.preload * max(1, sum(1 for v in valid if v))
            elif attempt and self.mode == "static_only" and static_fn is not None:
                self.target = np.asarray(static_fn, dtype=float).copy()
            elif attempt:
                chosen = solve_with_deadline(obs, static_fn, self.mode, budget, started)
                self.target = np.asarray(chosen["cmd"], dtype=float)
                self.chosen_total = float(np.sum(self.target))
                self.solver_ms = float(chosen.get("solver_ms", 0.0))
                if self.record_dynamic:
                    mass = float(np.clip(-float(obs["external_wrench_hat"][1]) / 9.81, 0.2, 0.8))
                    self.dynamic_report = classify_dynamic(
                        contacts,
                        obs["fn_applied"],
                        obs["external_wrench_hat"],
                        obs["object_position"],
                        obs["object_velocity"],
                        float(obs["object_theta"]),
                        float(obs["object_omega"]),
                        mass,
                        0.5 * mass * float(obs["radius"]) ** 2,
                        float(obs["dt"]),
                        budget,
                        self.target,
                    )
            if self.use_recontact:
                self.recontact_plan = predict_contacts(obs)
        stable = speed_now < 0.03 and abs(float(obs["object_omega"])) < 0.4 and float(np.linalg.norm(obs["object_position"])) < 0.012
        stop = False
        if self.dynamic_report is not None and self.decision_label in ("INFEASIBLE", "UNCERTAIN"):
            stop = True
        label = self.decision_label if self.machine.state == EVALUATE else None
        if label in ("INFEASIBLE", "UNCERTAIN") and not self.degrade_if_infeasible:
            label = "FEASIBLE"
        info = self.machine.step(lost_now, warning, label, stable, stop)
        state = str(info["state"])
        if self.degrade_if_infeasible and self.decision_label in ("INFEASIBLE", "UNCERTAIN") and state in (DEGRADE, TERMINAL, EVALUATE):
            if self.degrade_cmd is None:
                contacts = _contacts(obs)
                degraded = self.safe.command(
                    obs["fn_applied"], valid, bool(obs["support_available"]),
                    float(obs["object_position"][1]), float(obs["radius"]), float(obs["dt"]),
                    contacts=contacts, w_ext_hat=obs["external_wrench_hat"], mu_hat=obs["mu_hat"],
                    total_budget=budget,
                )
                self.degrade_cmd = np.asarray(degraded["cmd_fn"], dtype=float)
            cmd = self.degrade_cmd.copy()
            mode = "safe_degradation"
            recontact = [False] * self.num_contacts
        elif self.use_preload:
            cmd = np.array([self.preload if is_valid else 0.0 for is_valid in valid], dtype=float)
            mode = "rate_limited_preload"
            recontact = self._recontact_mask() if self.use_recontact else [False] * self.num_contacts
        elif state in (RECOVERY, VERIFIED) and self.target is not None:
            if self.mode != "static_only":
                from dynamic_force_allocator import geometry_targets
                shares = geometry_targets(
                    obs["normal"], obs["mu_hat"], valid, obs["external_wrench_hat"],
                    obs["object_velocity"], float(obs["object_omega"]), obs["position"], self.chosen_total,
                )
                for i, is_valid in enumerate(valid):
                    shares[i] = float(shares[i] if is_valid else 0.0)
                if budget is not None and float(np.sum(shares)) > float(budget):
                    shares *= float(budget) / max(float(np.sum(shares)), 1e-9)
                self.target = shares
            cmd = np.array(self.target, dtype=float)
            for i, is_valid in enumerate(valid):
                if not is_valid:
                    cmd[i] = 0.0
            mode = "predictive_recovery"
            recontact = self._recontact_mask()
        else:
            cmd = np.array(self.hold if np.any(self.hold) else obs["fn_applied"], dtype=float)
            for i, is_valid in enumerate(valid):
                if not is_valid:
                    cmd[i] = 0.0
            mode = "hold"
            recontact = self._recontact_mask() if self.use_recontact and any(not v for v in valid) else [False] * self.num_contacts
        if budget is not None:
            active_idx = [i for i, v in enumerate(valid) if v]
            if active_idx:
                total = float(sum(cmd[i] for i in active_idx))
                cap = float(obs["protocol_budget"]) * len(active_idx)
                if total > cap:
                    cmd *= cap / total
        self.prev_valid = valid
        return {
            "cmd_fn": cmd.tolist(),
            "recontact": recontact,
            "state": state,
            "mode": mode,
            "decision_label": self.decision_label,
            "feasibility_probability": None if self.estimate is None else self.estimate["probability"],
            "reasons": [] if self.estimate is None else list(self.estimate["reasons"]),
            "dynamic_report": self.dynamic_report,
            "solver_ms": self.solver_ms,
            "recontact_plan": self.recontact_plan,
        }

    def _recontact_mask(self) -> List[bool]:
        if not self.use_recontact:
            return [False] * self.num_contacts
        if not self.recontact_plan:
            return [False] * self.num_contacts
        mask = [False] * self.num_contacts
        for item in self.recontact_plan:
            mask[int(item["index"])] = bool(item["request"])
        return mask


class StaticFeasibilityController(PredictiveRecoveryController):
    """Archived behavior reduced to one static allocation, with no dynamic search and no recontact."""

    name = "P5T4StaticFeasibilityRecovery"

    def __init__(self, num_contacts: int, mode: str = "fixed_margin", p_low: float = 0.20, p_high: float = 0.65, seed: int = 1, **_ignored):
        super().__init__(
            num_contacts, mode="fixed_margin", p_low=p_low, p_high=p_high, seed=seed,
            use_recontact=False, degrade_if_infeasible=True, use_predictor=False,
        )
        self.mode = "static_only"

    def step(self, obs: Dict[str, object]) -> Dict[str, object]:
        # Static allocation is the nominal linear-program solution, not a margin search.
        command = super().step(obs)
        if self.estimate is not None and self.decision_label in ("FEASIBLE", "DEGRADED_RECOVERY"):
            nominal = (self.estimate.get("nominal_solution") or {}).get("fn")
            if nominal is not None and command["mode"] == "predictive_recovery":
                valid = [bool(v) for v in obs["valid"]]
                cmd = np.asarray(nominal, dtype=float).copy()
                for i, is_valid in enumerate(valid):
                    if not is_valid:
                        cmd[i] = 0.0
                command["cmd_fn"] = cmd.tolist()
                command["mode"] = "static_recovery"
                self.target = cmd
        command["recontact"] = [False] * self.num_contacts
        return command


class RateLimitedDynamicController(PredictiveRecoveryController):
    name = "P5T4RateLimitedDynamic"

    def __init__(self, num_contacts: int, mode: str = "preload_16", p_low: float = 0.20, p_high: float = 0.65, seed: int = 1, **_ignored):
        super().__init__(
            num_contacts, mode="preload_16", p_low=p_low, p_high=p_high, seed=seed,
            use_recontact=False, degrade_if_infeasible=True, use_predictor=False,
        )


class PredictiveRecontactController(PredictiveRecoveryController):
    """Predictive force and finger motion without the safe-degradation branch."""

    name = "P5T4PredictiveRecontact"

    def __init__(self, num_contacts: int, mode: str = "min_surviving", p_low: float = 0.20, p_high: float = 0.65, seed: int = 1, **_ignored):
        super().__init__(
            num_contacts, mode=mode if str(mode).startswith("preload_") else "preload_16", p_low=p_low, p_high=p_high, seed=seed,
            use_recontact=True, degrade_if_infeasible=False, use_predictor=True,
        )


class CorrectiveRecoveryController(PredictiveRecoveryController):
    name = "P5T4CorrectiveRecovery"

    def __init__(self, num_contacts: int, mode: str = "min_surviving", p_low: float = 0.20, p_high: float = 0.65, seed: int = 1, **_ignored):
        super().__init__(
            num_contacts, mode=mode if str(mode).startswith("preload_") else "preload_16", p_low=p_low, p_high=p_high, seed=seed,
            use_recontact=True, degrade_if_infeasible=True, use_predictor=True,
        )
        self.record_dynamic = True
