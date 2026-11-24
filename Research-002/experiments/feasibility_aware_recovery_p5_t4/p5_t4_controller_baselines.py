"""Fair baselines. None of them receive the oracle label or the true friction."""

from __future__ import annotations

from typing import Dict, List

import numpy as np


class BaselineController:
    name = "baseline"

    def __init__(self, num_contacts: int):
        self.num_contacts = int(num_contacts)
        self.prev_valid: List[bool] = [True] * self.num_contacts

    def reset(self, obs: Dict[str, object] | None = None):
        self.prev_valid = [True] * self.num_contacts if obs is None else [bool(v) for v in obs["valid"]]

    def _finalize(self, obs: Dict[str, object], cmd: np.ndarray, mode: str) -> Dict[str, object]:
        valid = [bool(v) for v in obs["valid"]]
        for i, is_valid in enumerate(valid):
            if not is_valid:
                cmd[i] = 0.0
        budget_unit = obs.get("protocol_budget")
        if budget_unit is not None:
            active = [i for i, v in enumerate(valid) if v]
            if active:
                cap = float(budget_unit) * len(active)
                total = float(sum(cmd[i] for i in active))
                if total > cap:
                    cmd *= cap / total
        cmd = np.clip(cmd, 0.0, 25.0)
        self.prev_valid = valid
        return {
            "cmd_fn": cmd.tolist(),
            "recontact": [False] * self.num_contacts,
            "state": "RECOVERY" if any(not v for v in valid) else "NORMAL",
            "mode": mode,
            "decision_label": None,
            "feasibility_probability": None,
            "reasons": [],
        }


class EqualForcePerContact(BaselineController):
    name = "EqualForcePerContact"

    def step(self, obs):
        cmd = np.array([8.0 if v else 0.0 for v in obs["valid"]], dtype=float)
        return self._finalize(obs, cmd, "equal")


class FixedTotalForce(BaselineController):
    name = "FixedTotalForce"

    def step(self, obs):
        n_nom = int(obs["num_contacts"])
        active = [i for i, v in enumerate(obs["valid"]) if v]
        cmd = np.zeros(self.num_contacts)
        if active:
            share = min(25.0, (n_nom * 8.0) / len(active))
            for i in active:
                cmd[i] = share
        return self._finalize(obs, cmd, "fixed_total")


class RuleBasedCooperative(BaselineController):
    name = "RuleBasedCooperative"

    def step(self, obs):
        cmd = np.asarray(obs["fn_applied"], dtype=float).copy()
        for i, is_valid in enumerate(obs["valid"]):
            if not is_valid:
                cmd[i] = 0.0
                continue
            mu = max(0.05, float(obs["mu_hat"][i]))
            margin = mu * max(cmd[i], 0.1) - abs(float(obs["ft"][i]))
            if margin < 0.4:
                cmd[i] = min(25.0, cmd[i] + 2.0)
            elif margin > 2.0:
                cmd[i] = max(0.0, cmd[i] - 0.5)
        return self._finalize(obs, cmd, "rule")


class PredictiveMarginBalancing(BaselineController):
    name = "PredictiveMarginBalancing"

    def step(self, obs):
        cmd = np.asarray(obs["fn_applied"], dtype=float).copy()
        for i, is_valid in enumerate(obs["valid"]):
            if not is_valid:
                cmd[i] = 0.0
                continue
            mu = max(0.05, float(obs["mu_hat"][i]))
            # Predict the margin after a small extra shear from the estimated disturbance.
            shear = abs(float(obs["external_wrench_hat"][0])) / max(1, sum(obs["valid"]))
            margin = mu * cmd[i] - abs(float(obs["ft"][i])) - 0.25 * shear
            cmd[i] = float(np.clip(cmd[i] + 0.8 * (1.2 - margin), 0.0, 25.0))
        return self._finalize(obs, cmd, "margin")


class IndependentP5T1Adaptive(BaselineController):
    name = "IndependentP5T1Adaptive"

    def step(self, obs):
        cmd = np.zeros(self.num_contacts)
        for i, is_valid in enumerate(obs["valid"]):
            if not is_valid:
                continue
            mu = float(obs["mu_hat"][i])
            cmd[i] = 12.0 if mu <= 0.40 else (8.0 if mu <= 0.70 else 5.0)
        return self._finalize(obs, cmd, "independent")


class P5T3LearnedUncertaintyAware(BaselineController):
    name = "P5T3LearnedUncertaintyAware"

    def step(self, obs):
        cmd = np.asarray(obs["fn_applied"], dtype=float).copy()
        for i, is_valid in enumerate(obs["valid"]):
            if not is_valid:
                cmd[i] = 0.0
                continue
            mu = max(0.05, float(obs["mu_hat"][i]))
            sigma = float(obs["mu_sigma"][i])
            margin = (mu - sigma) * cmd[i] - abs(float(obs["ft"][i]))
            cmd[i] = float(np.clip(cmd[i] + 1.0 * (1.5 - margin), 0.0, 25.0))
        return self._finalize(obs, cmd, "p5t3_learned")


class P5T3RecoveryNoFeasibility(BaselineController):
    name = "P5T3RecoveryNoFeasibility"

    def step(self, obs):
        active = [i for i, v in enumerate(obs["valid"]) if v]
        cmd = np.zeros(self.num_contacts)
        if active:
            # Redistribute the pre-loss total and add a squeeze, with no feasibility test.
            carried = float(sum(obs["fn_applied"])) * 1.3
            share = min(25.0, carried / len(active))
            for i in active:
                cmd[i] = max(share, 8.0)
        return self._finalize(obs, cmd, "squeeze")
