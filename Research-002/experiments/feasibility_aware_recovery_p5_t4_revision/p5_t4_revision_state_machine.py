"""Eight-state machine. A feasible recovery is not aborted just because the object has moved."""

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


class RevisionStateMachine:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = NORMAL
        self.steps_in_state = 0
        self.stable_steps = 0
        self.warning_count = 0
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
        stop_recovery: bool,
    ) -> Dict[str, object]:
        self.steps_in_state += 1
        if self.state == TERMINAL:
            return self._obs()
        if self.state == NORMAL:
            if contact_lost_now:
                self._set(LOSS, "contact_loss")
            elif warning:
                self.warning_count += 1
                if self.warning_count >= 5:
                    self._set(WARNING, "margin_warning")
            else:
                self.warning_count = 0
        elif self.state == WARNING:
            if contact_lost_now:
                self._set(LOSS, "warning_became_loss")
            elif self.steps_in_state >= 4:
                self._set(EVALUATE, "sustained_warning")
        elif self.state == LOSS:
            if self.steps_in_state >= 1:
                self._set(EVALUATE, "configuration_updated")
        elif self.state == EVALUATE:
            if feasibility_label in ("FEASIBLE", "DEGRADED_RECOVERY"):
                self._set(RECOVERY, "attempt_recovery")
            elif feasibility_label in ("INFEASIBLE", "UNCERTAIN"):
                self._set(DEGRADE, "stop_impossible_recovery")
            elif self.steps_in_state >= 2:
                self._set(DEGRADE, "no_decision")
        elif self.state == RECOVERY:
            if stop_recovery:
                self._set(DEGRADE, "dynamic_stop")
            elif stable_now:
                self.stable_steps += 1
                if self.stable_steps >= 12:
                    self._set(VERIFIED, "held")
            else:
                self.stable_steps = 0
        elif self.state == VERIFIED:
            if not stable_now:
                self._set(RECOVERY, "left_hold")
            elif self.steps_in_state >= 8:
                self._set(TERMINAL, "verified_hold")
        elif self.state == DEGRADE:
            if self.steps_in_state >= 2:
                self._set(TERMINAL, "degraded")
        return self._obs()

    def _obs(self) -> Dict[str, object]:
        return {"state": self.state, "steps_in_state": self.steps_in_state, "transitions": len(self.transitions)}
