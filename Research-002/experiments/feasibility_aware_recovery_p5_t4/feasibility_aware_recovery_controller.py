"""Feasibility-aware recovery. The allocator cannot outrank a failed feasibility check."""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from contact_wrench_analysis import ContactState
from feasibility_estimator import CausalFeasibilityEstimator
from p5_t4_state_machine import DEGRADE, EVALUATE, NORMAL, RECOVERY, TERMINAL, VERIFIED, WARNING, RecoveryStateMachine
from recovery_optimization import allocate_normal_forces
from safe_degradation_controller import SafeDegradationController


def contacts_from_observation(obs: Dict[str, object]) -> List[ContactState]:
    contacts = []
    for i in range(int(obs["num_contacts"])):
        contacts.append(ContactState(
            contact_id=i,
            position=np.asarray(obs["position"][i], dtype=float),
            normal=np.asarray(obs["normal"][i], dtype=float),
            tangent=np.asarray(obs["tangent"][i], dtype=float),
            mu=float(obs["mu_hat"][i]),
            fn=float(obs["fn_applied"][i]),
            valid=bool(obs["valid"][i]),
            displacement=float(obs["displacement"][i]),
            mu_sigma=float(obs["mu_sigma"][i]),
        ))
    return contacts


class FeasibilityAwareRecoveryController:
    name = "P5T4FeasibilityAwareRecovery"

    def __init__(self, num_contacts: int, residual_mode: str = "none", p_low: float = 0.35, p_high: float = 0.80, seed: int = 1):
        self.num_contacts = int(num_contacts)
        self.residual_mode = residual_mode
        self.machine = RecoveryStateMachine()
        self.estimator = CausalFeasibilityEstimator(p_low=p_low, p_high=p_high, seed=seed)
        self.safe = SafeDegradationController(num_contacts)
        self.reset_state()

    def reset(self, obs: Optional[Dict[str, object]] = None):
        self.machine.reset()
        self.estimator.reset(None if obs is None else int(obs.get("time", 0) * 1000) + self.num_contacts)
        self.safe.reset()
        self.reset_state()
        if obs is not None:
            self.prev_valid = [bool(v) for v in obs["valid"]]
            self.hold = np.asarray(obs["fn_applied"], dtype=float).copy()
            self.target = self.hold.copy()

    def reset_state(self):
        self.prev_valid: List[bool] = [True] * self.num_contacts
        self.hold = np.zeros(self.num_contacts)
        self.target = np.zeros(self.num_contacts)
        self.decided = False
        self.estimate: Optional[Dict[str, object]] = None
        self.decision_label: Optional[str] = None
        self.step_index = 0

    def step(self, obs: Dict[str, object]) -> Dict[str, object]:
        self.step_index += 1
        valid = [bool(v) for v in obs["valid"]]
        lost_now = any(prev and not now for prev, now in zip(self.prev_valid, valid))
        speed_now = float(np.linalg.norm(obs["object_velocity"]))
        if speed_now > 0.06 and not self.decided:
            lost_now = True
        regained = any((not prev) and now for prev, now in zip(self.prev_valid, valid))
        if regained:
            self.decided = False
            self.estimate = None
        warning = False
        for i, is_valid in enumerate(valid):
            if not is_valid:
                continue
            limit = abs(float(obs["mu_hat"][i])) * max(float(obs["fn"][i]), 0.1)
            if abs(float(obs["ft"][i])) > 0.85 * limit and limit > 0.2:
                warning = True
        contacts = contacts_from_observation(obs)
        n_active = sum(1 for v in valid if v)
        budget = None if obs.get("protocol_budget") is None else float(obs["protocol_budget"]) * max(n_active, 1)
        fn_max = 25.0 if budget is None else 25.0
        if self.machine.state == EVALUATE and not self.decided:
            speed = float(np.linalg.norm(obs["object_velocity"]))
            self.estimate = self.estimator.estimate(
                contacts,
                obs["mu_hat"],
                obs["mu_sigma"],
                np.asarray(obs["external_wrench_hat"], dtype=float),
                speed,
                budget,
            )
            self.decided = True
            self.decision_label = str(self.estimate["label"])
            if self.decision_label not in ("FEASIBLE", "DEGRADED_RECOVERY"):
                self.target = np.zeros(self.num_contacts)
        if self.decided and self.decision_label in ("FEASIBLE", "DEGRADED_RECOVERY"):
            wrench_goal = np.asarray(obs["external_wrench_hat"], dtype=float).copy()
            velocity = np.asarray(obs["object_velocity"], dtype=float)
            position = np.asarray(obs["object_position"], dtype=float)
            # Ask the contacts for a restoring wrench, not only a static balance.
            wrench_goal[0] += float(np.clip(4.0 * float(velocity[0]) + 8.0 * float(position[0]), -0.4, 0.4))
            wrench_goal[1] += float(np.clip(4.0 * float(velocity[1]) + 8.0 * float(position[1]), -0.4, 0.4))
            wrench_goal[2] += float(np.clip(0.004 * float(obs["object_omega"]) + 0.004 * float(obs["object_theta"]), -0.004, 0.004))
            refreshed = allocate_normal_forces(
                contacts,
                obs["mu_hat"],
                wrench_goal,
                budget,
                residual_mode=self.residual_mode,
                fn_max=fn_max,
            )
            if float(np.sum(refreshed)) > 0.05:
                self.target = refreshed
        speed = float(np.linalg.norm(obs["object_velocity"]))
        stable = speed < 0.03 and abs(float(obs["object_omega"])) < 0.4 and float(np.linalg.norm(obs["object_position"])) < 0.012
        label = self.decision_label if self.machine.state in (EVALUATE, RECOVERY) else None
        displacement = float(np.linalg.norm(obs["object_position"]))
        diverging = displacement > 0.020
        info = self.machine.step(lost_now, warning, label, stable, diverging)
        state = str(info["state"])
        recontact = [not v for v in valid]
        if state in (DEGRADE, TERMINAL) or (self.decision_label in ("INFEASIBLE", "UNCERTAIN") and state != NORMAL):
            degraded = self.safe.command(
                obs["fn_applied"], valid, bool(obs["support_available"]),
                float(obs["object_position"][1]), float(obs["radius"]), float(obs["dt"]),
                contacts=contacts, w_ext_hat=obs["external_wrench_hat"], mu_hat=obs["mu_hat"],
                total_budget=budget,
            )
            cmd = np.asarray(degraded["cmd_fn"], dtype=float)
            mode = degraded["mode"]
        elif state in (RECOVERY, VERIFIED):
            cmd = np.array(self.target, dtype=float)
            for i, is_valid in enumerate(valid):
                if not is_valid:
                    cmd[i] = 0.0
            mode = "recovery"
        else:
            cmd = np.array(self.hold if state == NORMAL else obs["fn_applied"], dtype=float)
            for i, is_valid in enumerate(valid):
                if not is_valid:
                    cmd[i] = 0.0
            mode = "hold"
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
        }


class SafeDegradationOnlyController(FeasibilityAwareRecoveryController):
    """On any contact loss, skip the recovery attempt and unload."""

    name = "P5T4SafeDegradation"

    def step(self, obs: Dict[str, object]) -> Dict[str, object]:
        valid = [bool(v) for v in obs["valid"]]
        lost_now = any(prev and (not now) for prev, now in zip(self.prev_valid, valid))
        if lost_now or any(not v for v in valid):
            self.decision_label = "INFEASIBLE"
            contacts = contacts_from_observation(obs)
            info = self.machine.step(True, False, "INFEASIBLE", False, False)
            # Force the machine toward degradation even if the transition takes a step.
            if info["state"] in ("LOSS_DETECTED", "EVALUATE", "NORMAL", "WARNING"):
                self.machine.state = "DEGRADE"
                info["state"] = "DEGRADE"
            n_active = sum(1 for v in valid if v)
            budget = None if obs.get("protocol_budget") is None else float(obs["protocol_budget"]) * max(n_active, 1)
            degraded = self.safe.command(
                obs["fn_applied"], valid, bool(obs["support_available"]),
                float(obs["object_position"][1]), float(obs["radius"]), float(obs["dt"]),
                contacts=contacts, w_ext_hat=obs["external_wrench_hat"], mu_hat=obs["mu_hat"],
                total_budget=budget,
            )
            self.prev_valid = valid
            return {
                "cmd_fn": degraded["cmd_fn"].tolist(),
                "recontact": [False] * self.num_contacts,
                "state": "DEGRADE",
                "mode": degraded["mode"],
                "decision_label": "INFEASIBLE",
                "feasibility_probability": 0.0,
                "reasons": ["policy_always_degrades_after_loss"],
            }
        self.prev_valid = valid
        return {
            "cmd_fn": [float(v) for v in obs["fn_applied"]],
            "recontact": [False] * self.num_contacts,
            "state": "NORMAL",
            "mode": "hold",
            "decision_label": None,
            "feasibility_probability": None,
            "reasons": [],
        }
