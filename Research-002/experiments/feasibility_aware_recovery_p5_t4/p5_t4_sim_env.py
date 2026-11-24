"""
Isolated P5-T4 planar grasp plant.

Certified Contact Physics v2 and Simulator v1 are not imported. Tangential
force is the friction-cone reaction closest to object wrench balance.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

from contact_feasibility_solver import reaction_friction, solve_bounded_wrench
from contact_wrench_analysis import (
    ObjectState,
    external_wrench,
    make_radial_contact,
    rotate_contacts,
)

FN_MAX = 25.0
SLEW = 50.0
GRAVITY = 9.81
DROP_M = 0.025
TILT_LIMIT = math.radians(20.0)


class FeasibilityGraspEnv:
    def __init__(self, scenario: Dict[str, object], protocol: str, dt: float = 0.005):
        self.scenario = scenario
        self.protocol = protocol
        self.dt = float(dt)
        self.rng = np.random.RandomState(int(scenario["seed"]) + 91)
        self.reset()

    def reset(self) -> Dict[str, object]:
        s = self.scenario
        com = np.asarray(s["com_offset"], dtype=float)
        self.template = []
        for i in range(int(s["num_contacts"])):
            contact = make_radial_contact(
                i,
                float(s["angles_rad"][i]),
                float(s["radius"]),
                float(s["mu"][i]),
                float(s["stiffness"][i]),
                com,
            )
            self.template.append(contact)
        self.base_mu = np.array(s["mu"], dtype=float).copy()
        self.obj = ObjectState(
            mass=float(s["mass"]),
            inertia=float(s["inertia"]),
            com_offset=com,
            external_force=np.asarray(s["disturbance_force"], dtype=float),
            external_torque=float(s["disturbance_torque"]),
            radius=float(s["radius"]),
        )
        self.obj.linear_velocity = np.asarray(s["initial_velocity"], dtype=float).copy()
        self.obj.angular_velocity = float(s["initial_omega"])
        self.fn = np.zeros(len(self.template))
        self.ft = np.zeros(len(self.template))
        self.prev_delta = np.zeros(len(self.template))
        self.lost = set()
        self.loss_pose = {}
        self.step_count = 0
        self.time = 0.0
        self.work_normal = 0.0
        self.work_tangent = 0.0
        self.impulse = 0.0
        self.damage = 0.0
        self.peak_fn = 0.0
        self.peak_speed = float(np.linalg.norm(self.obj.linear_velocity))
        self.peak_disp = 0.0
        self.impact_speed = None
        self.supported = False
        self.command_budget_violations = 0
        self.applied_budget_violations = 0
        self.applied_bound_violations = 0
        self.applied_slew_violations = 0
        self.consistency_violations = 0
        self.invalid_force_violations = 0
        self.recontact_attempts = 0
        self.recontact_successes = 0
        self.recontact_eta = {}
        self.escalation_events = 0
        self.degrade_reference_sum = None
        self.max_post_degrade_increase = 0.0
        self.saw_degrade = False
        w = external_wrench(self.obj)
        init = solve_bounded_wrench(self.template, w, fn_min=0.0, fn_max=FN_MAX, total_budget=None, spread_load=True)
        if init["feasible"]:
            self.fn = np.asarray(init["fn"], dtype=float).copy()
        else:
            self.fn[:] = 8.0
        reaction = reaction_friction(self.template, self.fn, w)
        self.ft = np.asarray(reaction["ft"], dtype=float)
        self.mu_hat = np.clip(self.base_mu + self.rng.normal(0.0, float(s["mu_noise"]), size=len(self.template)), 0.02, 1.4)
        self.mu_sigma = np.full(len(self.template), float(s["mu_noise"]))
        self.angle_bias = self.rng.normal(0.0, float(s["angle_noise_rad"]), size=len(self.template))
        self.mass_hat = float(self.obj.mass * (1.0 + self.rng.normal(0.0, 0.03)))
        self.dist_hat = np.asarray(s["disturbance_force"], dtype=float) + self.rng.normal(0.0, 0.15, size=2)
        self.tau_hat = float(s["disturbance_torque"] + self.rng.normal(0.0, 0.002))
        return self.observe()

    def _event(self):
        s = self.scenario
        kind = s["event"]
        t = self.time
        t0 = float(s["event_time"])
        if kind in ("sudden_invalidation", "multi_loss") and t >= t0:
            for idx in s["lost_ids"]:
                self._lose(int(idx))
        elif kind == "friction_collapse" and t >= t0:
            for idx in s["lost_ids"]:
                self.template[int(idx)].mu = float(self.base_mu[int(idx)] * float(s["collapse_factor"]))
        elif kind == "gradual":
            if t0 <= t <= t0 + float(s["event_duration"]):
                alpha = (t - t0) / max(1e-6, float(s["event_duration"]))
                for idx in s["lost_ids"]:
                    self.template[int(idx)].mu = float(self.base_mu[int(idx)] * (1.0 - 0.75 * alpha))
            elif t > t0 + float(s["event_duration"]):
                for idx in s["lost_ids"]:
                    self.template[int(idx)].mu = float(self.base_mu[int(idx)] * 0.25)
        elif kind == "stiffness_change" and t >= t0:
            for idx in s["lost_ids"]:
                self.template[int(idx)].stiffness = float(s["stiffness"][int(idx)] * float(s["stiffness_factor"]))
                self._lose(int(idx))
        elif kind == "object_motion" and t >= t0:
            for idx in s["lost_ids"]:
                self._lose(int(idx))

    def _lose(self, idx: int):
        if idx in self.lost:
            return
        self.lost.add(idx)
        self.template[idx].valid = False
        self.loss_pose[idx] = (self.obj.position.copy(), float(self.obj.theta))

    def _try_recontact(self, requests: Sequence[bool]):
        for i, want in enumerate(requests):
            if not want or i not in self.lost:
                continue
            if i not in self.recontact_eta:
                # The finger retracts and can return no sooner than 0.40 s.
                self.recontact_eta[i] = self.time + 0.40
                self.recontact_attempts += 1
            if self.time + 1e-12 < self.recontact_eta[i]:
                continue
            origin_pos, origin_theta = self.loss_pose[i]
            # Surface motion along the outward normal since the loss.
            c = math.cos(self.obj.theta)
            s = math.sin(self.obj.theta)
            rot = np.array([[c, -s], [s, c]])
            outward = -(rot @ self.template[i].normal)
            c0 = math.cos(origin_theta)
            s0 = math.sin(origin_theta)
            rot0 = np.array([[c0, -s0], [s0, c0]])
            point_now = self.obj.position + rot @ (self.template[i].position)
            point_then = origin_pos + rot0 @ (self.template[i].position)
            separation = float(np.dot(point_now - point_then, outward))
            if separation <= 0.005 and abs(self.obj.linear_velocity @ outward) < 0.05:
                self.template[i].valid = True
                self.template[i].mu = float(self.base_mu[i])
                self.lost.discard(i)
                self.recontact_successes += 1

    def step(self, command: Dict[str, object]):
        self._event()
        cmd = np.asarray(command["cmd_fn"], dtype=float).copy()
        requests = list(command.get("recontact", [False] * len(self.template)))
        self._try_recontact(requests)
        state_name = str(command.get("state", ""))
        if state_name in ("DEGRADE", "TERMINAL"):
            if not self.saw_degrade:
                self.saw_degrade = True
                self.degrade_reference_sum = float(np.sum(cmd))
            else:
                increase = float(np.sum(cmd) - self.degrade_reference_sum)
                self.max_post_degrade_increase = max(self.max_post_degrade_increase, increase)
                if increase > 0.25:
                    self.escalation_events += 1

        previous = self.fn.copy()
        proposed = self.fn.copy()
        n_active = 0
        for i, contact in enumerate(self.template):
            if not contact.valid:
                proposed[i] = 0.0
                continue
            n_active += 1
            raw = float(np.clip(cmd[i], 0.0, FN_MAX))
            delta = float(np.clip(raw - self.fn[i], -SLEW * self.dt, SLEW * self.dt))
            proposed[i] = float(np.clip(self.fn[i] + delta, 0.0, FN_MAX))
            if abs((proposed[i] - previous[i]) / self.dt) > SLEW + 1e-6:
                self.applied_slew_violations += 1
            if proposed[i] < -1e-8 or proposed[i] > FN_MAX + 1e-6:
                self.applied_bound_violations += 1
        budget = n_active * 8.0
        pre_budget_sum = float(np.sum(proposed))
        if self.protocol == "Protocol_B" and n_active > 0 and pre_budget_sum > budget + 1e-6:
            self.command_budget_violations += 1
            proposed *= budget / pre_budget_sum
        if self.protocol == "Protocol_B" and n_active > 0 and float(np.sum(proposed)) > budget + 1e-5:
            self.applied_budget_violations += 1
        for i, contact in enumerate(self.template):
            if not contact.valid and proposed[i] > 1e-8:
                self.invalid_force_violations += 1
            self.fn[i] = proposed[i]
            contact.fn = proposed[i]
            contact.displacement = proposed[i] / max(contact.stiffness, 1.0)
            d_delta = contact.displacement - self.prev_delta[i]
            if d_delta > 0.0 and contact.valid:
                self.work_normal += proposed[i] * d_delta
            self.prev_delta[i] = contact.displacement

        world = rotate_contacts(self.template, self.obj.theta)
        w_ext = external_wrench(self.obj)
        reaction = reaction_friction(world, self.fn, w_ext)
        self.ft = np.asarray(reaction["ft"], dtype=float)
        resid = np.asarray(reaction["residual"], dtype=float)
        for i, contact in enumerate(self.template):
            contact.ft = float(self.ft[i])
            if contact.valid and abs(self.ft[i]) > contact.mu * self.fn[i] + 1e-4:
                self.consistency_violations += 1
        if not np.all(np.isfinite(resid)):
            self.consistency_violations += 1

        # Slip update of the causal friction estimate. Uses the measured force ratio only.
        for i, contact in enumerate(world):
            if not contact.valid or self.fn[i] < 0.5:
                continue
            v_point = self.obj.linear_velocity + self.obj.angular_velocity * np.array([-contact.position[1], contact.position[0]])
            slip = abs(float(np.dot(v_point, contact.tangent)))
            if slip > 0.002:
                ratio = abs(self.ft[i]) / max(self.fn[i], 1e-3)
                noisy = float(np.clip(ratio + self.rng.normal(0.0, 0.02), 0.02, 1.4))
                self.mu_hat[i] = 0.8 * self.mu_hat[i] + 0.2 * noisy

        force = resid[:2] - w_ext[:2]  # contact force; residual is contact + external
        # Integrate the contact wrench plus external wrench, with light damping.
        contact_force = resid[:2] - w_ext[:2]
        contact_tau = float(resid[2] - w_ext[2])
        net_force = contact_force + w_ext[:2] - 0.2 * self.obj.linear_velocity
        net_tau = contact_tau + w_ext[2] - 0.001 * self.obj.angular_velocity
        if not np.allclose(net_force, resid[:2] - 0.2 * self.obj.linear_velocity, atol=1e-8):
            self.consistency_violations += 1
        self.obj.linear_velocity = self.obj.linear_velocity + self.dt * (net_force / self.obj.mass)
        self.obj.angular_velocity = self.obj.angular_velocity + self.dt * (net_tau / self.obj.inertia)
        self.obj.position = self.obj.position + self.dt * self.obj.linear_velocity
        self.obj.theta = self.obj.theta + self.dt * self.obj.angular_velocity

        self._support_contact()
        speed = float(np.linalg.norm(self.obj.linear_velocity))
        self.peak_speed = max(self.peak_speed, speed)
        disp = float(np.linalg.norm(self.obj.position))
        self.peak_disp = max(self.peak_disp, disp)
        self.peak_fn = max(self.peak_fn, float(np.max(self.fn)) if len(self.fn) else 0.0)
        self.impulse += float(np.sum(self.fn)) * self.dt
        self.damage += float(np.sum(np.maximum(0.0, self.fn - 15.0) ** 2)) * self.dt
        self.work_tangent += float(np.sum(np.abs(self.ft) * 0.0))  # reactions are not commanded actuator work
        # Tangential mechanical work uses slip power once velocity is known.
        for i, contact in enumerate(world):
            if not contact.valid:
                continue
            v_point = self.obj.linear_velocity + self.obj.angular_velocity * np.array([-contact.position[1], contact.position[0]])
            slip = float(np.dot(v_point, contact.tangent))
            self.work_tangent += abs(float(self.ft[i]) * slip) * self.dt

        self.step_count += 1
        self.time += self.dt
        done = self.step_count >= int(self.scenario["horizon_steps"])
        info = self.metrics()
        info["done"] = done
        return self.observe(), info

    def _support_contact(self):
        if not bool(self.scenario["support_available"]):
            return
        floor = -(float(self.scenario["radius"]) + 0.015)
        if self.obj.position[1] <= floor:
            if not self.supported:
                self.impact_speed = abs(float(self.obj.linear_velocity[1]))
                self.supported = True
            self.obj.position[1] = floor
            if self.obj.linear_velocity[1] < 0.0:
                self.obj.linear_velocity[1] = 0.0
            if abs(self.obj.linear_velocity[0]) > 0.0:
                friction = min(abs(self.obj.linear_velocity[0]), 0.4 * GRAVITY * self.dt)
                self.obj.linear_velocity[0] -= math.copysign(friction, self.obj.linear_velocity[0])

    def observe(self) -> Dict[str, object]:
        world = rotate_contacts(self.template, self.obj.theta)
        fn_meas = self.fn + self.rng.normal(0.0, 0.05, size=len(self.fn))
        ft_meas = self.ft + self.rng.normal(0.0, 0.05, size=len(self.ft))
        positions = []
        normals = []
        tangents = []
        valid = []
        for i, contact in enumerate(world):
            c = math.cos(self.angle_bias[i])
            s = math.sin(self.angle_bias[i])
            rot = np.array([[c, -s], [s, c]])
            normals.append((rot @ contact.normal).tolist())
            tangents.append((rot @ contact.tangent).tolist())
            positions.append((contact.position + self.rng.normal(0.0, 0.0004, size=2)).tolist())
            valid.append(bool(self.template[i].valid))
        w_hat = np.array([
            float(self.dist_hat[0]),
            float(self.dist_hat[1] - self.mass_hat * GRAVITY),
            float(self.tau_hat),
        ])
        return {
            "fn": fn_meas.tolist(),
            "ft": ft_meas.tolist(),
            "mu_hat": self.mu_hat.tolist(),
            "mu_sigma": self.mu_sigma.tolist(),
            "valid": valid,
            "position": positions,
            "normal": normals,
            "tangent": tangents,
            "displacement": [c.displacement for c in self.template],
            "object_position": self.obj.position.tolist(),
            "object_theta": float(self.obj.theta),
            "object_velocity": self.obj.linear_velocity.tolist(),
            "object_omega": float(self.obj.angular_velocity),
            "external_wrench_hat": w_hat.tolist(),
            "support_available": bool(self.scenario["support_available"]),
            "radius": float(self.scenario["radius"]),
            "dt": self.dt,
            "fn_applied": self.fn.tolist(),
            "num_contacts": len(self.template),
            "protocol_budget": None if self.protocol != "Protocol_B" else 8.0,
            "time": self.time,
        }

    def metrics(self) -> Dict[str, object]:
        speed = float(np.linalg.norm(self.obj.linear_velocity))
        active = int(sum(1 for c in self.template if c.valid))
        held = (
            self.peak_disp < DROP_M
            and abs(self.obj.theta) < TILT_LIMIT
            and speed < 0.05
            and active >= 2
            and not self.supported
        )
        return {
            "time": self.time,
            "grasp_held": bool(held),
            "active_contacts": active,
            "displacement_m": self.peak_disp,
            "tilt_rad": abs(float(self.obj.theta)),
            "speed": speed,
            "peak_speed": self.peak_speed,
            "peak_fn": self.peak_fn,
            "sum_fn": float(np.sum(self.fn)),
            "impulse": self.impulse,
            "work_normal": self.work_normal,
            "work_tangent": self.work_tangent,
            "damage": self.damage,
            "impact_speed": self.impact_speed,
            "supported": self.supported,
            "command_budget_violations": self.command_budget_violations,
            "applied_budget_violations": self.applied_budget_violations,
            "applied_bound_violations": self.applied_bound_violations,
            "applied_slew_violations": self.applied_slew_violations,
            "consistency_violations": self.consistency_violations,
            "invalid_force_violations": self.invalid_force_violations,
            "recontact_attempts": self.recontact_attempts,
            "recontact_successes": self.recontact_successes,
            "escalation_events": self.escalation_events,
            "max_post_degrade_increase": self.max_post_degrade_increase,
            "final_position": self.obj.position.tolist(),
            "final_velocity": self.obj.linear_velocity.tolist(),
        }
