"""Topology-aware recovery. The per-contact level is chosen on the training split, not at 25 N."""

from __future__ import annotations

import _paths  # noqa: F401
from typing import Dict, List, Optional

import numpy as np

from p5_t4r2_recontact_planner import predict_contacts
from contact_wrench_analysis import ContactState
from dynamic_contact_feasibility import classify
from p5_t4r2_force_allocator import allocate
from feasibility_estimator import CausalFeasibilityEstimator
from p5_t4_revision_state_machine import DEGRADE, EVALUATE, RECOVERY, TERMINAL, VERIFIED, RevisionStateMachine
from safe_degradation_controller import SafeDegradationController


def _contacts(obs: Dict[str, object]) -> List[ContactState]:
    built = []
    for index in range(int(obs["num_contacts"])):
        built.append(ContactState(
            contact_id=index,
            position=np.asarray(obs["position"][index], dtype=float),
            normal=np.asarray(obs["normal"][index], dtype=float),
            tangent=np.asarray(obs["tangent"][index], dtype=float),
            mu=float(obs["mu_hat"][index]),
            fn=float(obs["fn_applied"][index]),
            valid=bool(obs["valid"][index]),
            mu_sigma=float(obs["mu_sigma"][index]),
        ))
    return built


class TopologyAwareRecoveryController:
    name = "P5T4R2TopologyAware"

    def __init__(self, num_contacts: int, per_contact: float = 10.0, seed: int = 1):
        self.num_contacts = int(num_contacts)
        self.per_contact = float(per_contact)
        self.machine = RevisionStateMachine()
        self.estimator = CausalFeasibilityEstimator(p_low=0.20, p_high=0.65, seed=seed)
        self.safe = SafeDegradationController(num_contacts)
        self.reset(None)

    def reset(self, obs: Optional[Dict[str, object]] = None):
        self.machine.reset()
        self.safe.reset()
        self.estimator.reset(None if obs is None else self.num_contacts + 1)
        self.prev_valid = [True] * self.num_contacts if obs is None else [bool(v) for v in obs["valid"]]
        self.decided = False
        self.decision_label = None
        self.estimate = None
        self.degrade_cmd = None
        self.dynamic = None
        self.plan: List[Dict[str, object]] = []

    def step(self, obs: Dict[str, object]) -> Dict[str, object]:
        valid = [bool(v) for v in obs["valid"]]
        lost_now = any(prev and not now for prev, now in zip(self.prev_valid, valid))
        speed = float(np.linalg.norm(obs["object_velocity"]))
        if speed > 0.06 and not self.decided:
            lost_now = True
        warning = False
        for index, is_valid in enumerate(valid):
            if not is_valid:
                continue
            limit = abs(float(obs["mu_hat"][index])) * max(float(obs["fn"][index]), 0.1)
            if abs(float(obs["ft"][index])) > 0.85 * limit and limit > 0.2:
                warning = True
        n_active = sum(1 for v in valid if v)
        budget = None if obs.get("protocol_budget") is None else float(obs["protocol_budget"]) * max(n_active, 1)
        if self.machine.state == EVALUATE and not self.decided:
            contacts = _contacts(obs)
            self.estimate = self.estimator.estimate(
                contacts, obs["mu_hat"], obs["mu_sigma"],
                np.asarray(obs["external_wrench_hat"], dtype=float), speed, budget,
            )
            self.decided = True
            self.decision_label = str(self.estimate["label"])
            self.plan = predict_contacts(obs)
            self.dynamic = classify(
                contacts, obs["fn_applied"], obs["external_wrench_hat"], speed,
                float(np.linalg.norm(obs["object_position"])),
                any(item["request"] for item in self.plan), budget,
            )
        stable = speed < 0.03 and abs(float(obs["object_omega"])) < 0.4 and float(np.linalg.norm(obs["object_position"])) < 0.012
        label = self.decision_label if self.machine.state == EVALUATE else None
        info = self.machine.step(lost_now, warning, label, stable, False)
        state = str(info["state"])
        recontact = [bool(item["request"]) for item in self.plan] if self.plan else [not v for v in valid]
        if len(recontact) < self.num_contacts:
            recontact = recontact + [False] * (self.num_contacts - len(recontact))
        if self.decision_label in ("INFEASIBLE", "UNCERTAIN") and state in (DEGRADE, TERMINAL, EVALUATE):
            if self.degrade_cmd is None:
                degraded = self.safe.command(
                    obs["fn_applied"], valid, bool(obs["support_available"]),
                    float(obs["object_position"][1]), float(obs["radius"]), float(obs["dt"]),
                    contacts=_contacts(obs), w_ext_hat=obs["external_wrench_hat"], mu_hat=obs["mu_hat"],
                    total_budget=budget,
                )
                self.degrade_cmd = np.asarray(degraded["cmd_fn"], dtype=float)
            cmd = self.degrade_cmd.copy()
            mode = "safe_degradation"
            recontact = [False] * self.num_contacts
        else:
            cmd = allocate(obs["normal"], valid, obs["external_wrench_hat"], self.per_contact, budget)
            mode = "topology"
        self.prev_valid = valid
        return {
            "cmd_fn": cmd.tolist(),
            "recontact": recontact,
            "state": state,
            "mode": mode,
            "decision_label": self.decision_label,
            "feasibility_probability": None if self.estimate is None else self.estimate["probability"],
            "reasons": [] if self.estimate is None else list(self.estimate["reasons"]),
            "dynamic_report": self.dynamic,
            "solver_ms": 0.0,
            "recontact_plan": self.plan,
        }
