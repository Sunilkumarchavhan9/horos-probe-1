"""Per-contact lifecycle. Physics validity is not the same state as verified recovery."""

from __future__ import annotations

from typing import Dict

ACTIVE = "ACTIVE"
LOSS_SCHEDULED = "LOSS_SCHEDULED"
LOSS_TRIGGERED = "LOSS_TRIGGERED"
INACTIVE = "INACTIVE"
FINGER_APPROACHING = "FINGER_APPROACHING"
CONTACT_FORMING = "CONTACT_FORMING"
RECONTACTED = "RECONTACTED"
STABILIZING = "STABILIZING"
RECOVERY_VERIFIED = "RECOVERY_VERIFIED"
PERMANENTLY_UNAVAILABLE = "PERMANENTLY_UNAVAILABLE"


class ContactLifecycle:
    def __init__(self, count: int):
        self.state = [ACTIVE] * int(count)
        self.persist = [0] * int(count)
        self.max_persist = [0] * int(count)
        self.transitions = []

    def _set(self, index: int, new_state: str, reason: str):
        if self.state[index] == new_state:
            return
        self.transitions.append({"contact": index, "from": self.state[index], "to": new_state, "reason": reason})
        self.state[index] = new_state

    def mark_scheduled(self, index: int):
        if self.state[index] == ACTIVE:
            self._set(index, LOSS_SCHEDULED, "event_pending")

    def mark_loss(self, index: int, permanent: bool):
        self.persist[index] = 0
        if permanent:
            self._set(index, PERMANENTLY_UNAVAILABLE, "obstruction")
        else:
            self._set(index, LOSS_TRIGGERED, "event")
            self._set(index, INACTIVE, "contact_open")

    def mark_approach(self, index: int):
        if self.state[index] == PERMANENTLY_UNAVAILABLE:
            return
        self._set(index, FINGER_APPROACHING, "finger_moving")

    def mark_forming(self, index: int):
        if self.state[index] == PERMANENTLY_UNAVAILABLE:
            return
        self._set(index, CONTACT_FORMING, "gap_closed")

    def mark_loaded(self, index: int, loaded_steps: int, verify_steps: int):
        if self.state[index] == PERMANENTLY_UNAVAILABLE:
            return
        self.persist[index] = int(loaded_steps)
        self.max_persist[index] = max(self.max_persist[index], int(loaded_steps))
        if loaded_steps >= verify_steps:
            self._set(index, RECOVERY_VERIFIED, "persisted")
        elif loaded_steps >= 5:
            self._set(index, STABILIZING, "force_held")
        else:
            self._set(index, RECONTACTED, "force_started")

    def snapshot(self) -> Dict[str, object]:
        return {
            "state": list(self.state),
            "max_persist": list(self.max_persist),
            "transitions": len(self.transitions),
        }
