"""Eight-state recovery machine with dwell time so labels cannot chatter."""

from __future__ import annotations

from typing import Dict, Optional


NORMAL = "NORMAL"
WARNING = "WARNING"
LOSS = "LOSS_DETECTED"
EVALUATE = "EVALUATE"
RECOVERY = "RECOVERY"
DEGRADE = "DEGRADE"
VERIFIED = "VERIFIED"
TERMINAL = "TERMINAL"


class RecoveryStateMachine:
    def __init__(self, dwell_steps: int = 8, verify_steps: int = 25, warning_steps: int = 5):
        self.dwell_steps = int(dwell_steps)
        self.verify_steps = int(verify_steps)
        self.warning_steps = int(warning_steps)
        self.reset()

    def reset(self):
        self.state = NORMAL
        self.steps_in_state = 0
        self.stable_steps = 0
        self.warning_count = 0
        self.decision_label: Optional[str] = None
        self.transitions = []

    def _set(self, new_state: str, reason: str):
        if new_state != self.state:
            self.transitions.append({"from": self.state, "to": new_state, "reason": reason})
            self.state = new_state
            self.steps_in_state = 0
            if new_state != RECOVERY:
                self.stable_steps = 0

    def step(
        self,
        contact_lost_now: bool,
        warning: bool,
        feasibility_label: Optional[str],
        stable_now: bool,
        diverging: bool,
    ) -> Dict[str, object]:
        self.steps_in_state += 1
        if self.state == TERMINAL:
            return self._obs()
        if diverging and self.state in (RECOVERY, EVALUATE):
            self._set(DEGRADE, "diverging")
            return self._obs()

        if self.state == NORMAL:
            if contact_lost_now:
                self._set(LOSS, "contact_loss")
            elif warning:
                self.warning_count += 1
                if self.warning_count >= self.warning_steps:
                    self._set(WARNING, "degradation_warning")
            else:
                self.warning_count = 0
        elif self.state == WARNING:
            if contact_lost_now:
                self._set(LOSS, "warning_became_loss")
            elif warning and self.steps_in_state >= self.dwell_steps:
                self._set(EVALUATE, "sustained_warning")
            elif not warning and self.steps_in_state >= self.dwell_steps:
                self._set(NORMAL, "warning_cleared")
        elif self.state == LOSS:
            if self.steps_in_state >= 2:
                self._set(EVALUATE, "configuration_updated")
        elif self.state == EVALUATE:
            self.decision_label = feasibility_label
            if feasibility_label == "FEASIBLE":
                self._set(RECOVERY, "feasible")
            elif feasibility_label == "DEGRADED_RECOVERY":
                self._set(RECOVERY, "degraded_recovery")
            elif feasibility_label in ("INFEASIBLE", "UNCERTAIN", None):
                self._set(DEGRADE, "not_feasible" if feasibility_label != "UNCERTAIN" else "uncertain_fallback")
        elif self.state == RECOVERY:
            if feasibility_label == "INFEASIBLE":
                self._set(DEGRADE, "recovery_became_infeasible")
            elif stable_now:
                self.stable_steps += 1
                if self.stable_steps >= self.verify_steps:
                    self._set(VERIFIED, "stability_hold")
            else:
                self.stable_steps = 0
        elif self.state == DEGRADE:
            if self.steps_in_state >= 4:
                self._set(TERMINAL, "degradation_complete")
        elif self.state == VERIFIED:
            if contact_lost_now:
                self._set(LOSS, "new_loss")
            elif self.steps_in_state >= self.dwell_steps:
                self._set(NORMAL, "return_to_normal")
        return self._obs()

    def _obs(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "steps_in_state": self.steps_in_state,
            "decision_label": self.decision_label,
            "n_transitions": len(self.transitions),
        }
