"""Use whatever upward force the remaining contacts can still produce, and do not increase past it."""

from __future__ import annotations

from typing import Dict, Optional, Sequence

import numpy as np

from contact_feasibility_solver import maximum_upward_support
from contact_wrench_analysis import ContactState


class SafeDegradationController:
    name = "P5T4SafeDegradation"

    def __init__(self, num_contacts: int):
        self.num_contacts = int(num_contacts)
        self.peak_command = 0.0
        self.ceiling = None

    def reset(self):
        self.peak_command = 0.0
        self.ceiling = None

    def command(
        self,
        fn_applied: Sequence[float],
        valid: Sequence[bool],
        support_available: bool,
        object_y: float,
        radius: float,
        dt: float,
        contacts: Optional[Sequence[ContactState]] = None,
        w_ext_hat: Optional[Sequence[float]] = None,
        mu_hat: Optional[Sequence[float]] = None,
        total_budget: Optional[float] = None,
    ) -> Dict[str, object]:
        target = np.zeros(self.num_contacts)
        if contacts is not None and w_ext_hat is not None and mu_hat is not None:
            override = [max(0.02, float(mu_hat[c.contact_id])) for c in contacts if c.valid]
            support_fn = maximum_upward_support(
                contacts,
                np.asarray(w_ext_hat, dtype=float),
                total_budget=total_budget,
                mu_override=override,
            )
            for i, is_valid in enumerate(valid):
                if is_valid:
                    target[i] = float(support_fn[i])
            applied_sum = float(sum(float(fn_applied[i]) for i, is_valid in enumerate(valid) if is_valid))
            if self.ceiling is None:
                self.ceiling = applied_sum
            self.ceiling = min(self.ceiling, applied_sum)
            if float(np.sum(target)) > self.ceiling and self.ceiling >= 0.0:
                scale = self.ceiling / max(float(np.sum(target)), 1e-9)
                target *= scale
        self.peak_command = max(self.peak_command, float(np.sum(target)))
        return {
            "cmd_fn": target,
            "recontact": [False] * self.num_contacts,
            "mode": "partial_support" if float(np.sum(target)) > 0.2 else "controlled_release",
        }
