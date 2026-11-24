"""Finger travel and the loaded-contact counter. The scheduler does not clear a formed contact."""

from __future__ import annotations

import math
from typing import Dict

import numpy as np

from contact_geometry_validator import formation_stage
from p5_t4r2_experiment_protocol import FINGER_SPEED, FORM_FN, PERSIST_STEPS, RETRACT_M


class PersistentContactManager:
    def __init__(self):
        self.fingers: Dict[int, Dict[str, object]] = {}
        self.loaded_steps: Dict[int, int] = {}
        self.formed = set()
        self.attempts = 0
        self.formations = 0
        self.verified = 0

    def approach(self, env, index: int) -> str:
        contact = env.template[index]
        c = math.cos(env.obj.theta)
        s = math.sin(env.obj.theta)
        rot = np.array([[c, -s], [s, c]])
        point = env.obj.position + rot @ contact.position
        outward = -(rot @ contact.normal)
        if index not in self.fingers:
            self.fingers[index] = {"pos": point.copy(), "phase": "retract", "retract": 0.0}
            self.attempts += 1
        finger = self.fingers[index]
        pos = np.asarray(finger["pos"], dtype=float)
        if finger["phase"] == "retract":
            step = min(FINGER_SPEED * env.dt, RETRACT_M - float(finger["retract"]))
            pos = pos + outward * max(0.0, step)
            finger["retract"] = float(finger["retract"]) + max(0.0, step)
            if finger["retract"] >= RETRACT_M - 1e-9:
                finger["phase"] = "approach"
        else:
            delta = point - pos
            dist = float(np.linalg.norm(delta))
            step = FINGER_SPEED * env.dt
            if dist <= step:
                pos = point.copy()
            elif dist > 1e-9:
                pos = pos + delta / dist * step
        finger["pos"] = pos
        gap = float(np.linalg.norm(point - pos))
        closing = float(np.dot(env.obj.linear_velocity, outward))
        stage = formation_stage(gap, closing, float(env.fn[index]), FINGER_SPEED)
        if stage in ("forming", "loaded") and index not in env.lost:
            return stage
        if stage in ("forming", "loaded"):
            contact.valid = True
            contact.mu = float(env.base_mu[index])
            env.lost.discard(index)
            if index not in self.formed:
                env.fn[index] = 0.0
                self.formed.add(index)
                self.formations += 1
        return stage

    def account(self, env):
        for index, contact in enumerate(env.template):
            if index in self.formed and contact.valid and float(env.fn[index]) >= FORM_FN:
                self.loaded_steps[index] = self.loaded_steps.get(index, 0) + 1
                if self.loaded_steps[index] == PERSIST_STEPS:
                    self.verified += 1
            elif index in self.formed and not contact.valid:
                self.loaded_steps[index] = 0
