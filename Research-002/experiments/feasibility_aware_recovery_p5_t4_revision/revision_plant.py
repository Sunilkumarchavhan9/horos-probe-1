"""Archived integrator with a finger that has to travel back to the surface."""

from __future__ import annotations

import _paths  # noqa: F401
import math

import numpy as np

from contact_reestablishment_planner import FINGER_SPEED, RETRACT_M
from p5_t4_sim_env import FeasibilityGraspEnv


class PredictiveGraspEnv(FeasibilityGraspEnv):
    def reset(self):
        obs = super().reset()
        self.fingers = {}
        self.peak_omega = abs(float(self.obj.angular_velocity))
        return obs

    def _try_recontact(self, requests):
        for i, want in enumerate(requests):
            if i not in self.lost or not want:
                continue
            c = math.cos(self.obj.theta)
            s = math.sin(self.obj.theta)
            rot = np.array([[c, -s], [s, c]])
            point = self.obj.position + rot @ self.template[i].position
            outward = -(rot @ self.template[i].normal)
            if i not in self.fingers:
                self.fingers[i] = {"pos": point.copy(), "phase": "retract", "retract": 0.0}
                self.recontact_attempts += 1
            finger = self.fingers[i]
            pos = np.asarray(finger["pos"], dtype=float)
            if finger["phase"] == "retract":
                step = min(FINGER_SPEED * self.dt, RETRACT_M - float(finger["retract"]))
                pos = pos + outward * max(0.0, step)
                finger["retract"] = float(finger["retract"]) + max(0.0, step)
                if finger["retract"] >= RETRACT_M - 1e-9:
                    finger["phase"] = "approach"
            else:
                delta = point - pos
                dist = float(np.linalg.norm(delta))
                step = FINGER_SPEED * self.dt
                if dist <= step:
                    pos = point.copy()
                elif dist > 1e-9:
                    pos = pos + delta / dist * step
            finger["pos"] = pos
            gap = float(np.linalg.norm(point - pos))
            closing = float(np.dot(self.obj.linear_velocity, outward))
            if finger["phase"] == "approach" and gap <= 0.0015 and abs(closing) < 0.08:
                self.template[i].valid = True
                self.template[i].mu = float(self.base_mu[i])
                self.lost.discard(i)
                self.fn[i] = 0.0
                self.recontact_successes += 1

    def step(self, command):
        obs, info = super().step(command)
        self.peak_omega = max(self.peak_omega, abs(float(self.obj.angular_velocity)))
        info["peak_omega"] = self.peak_omega
        return obs, info
