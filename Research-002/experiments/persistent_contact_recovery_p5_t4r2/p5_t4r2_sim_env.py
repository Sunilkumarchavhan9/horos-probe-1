"""Archived integrator with explicit one-shot, obstruction, and repeated-event semantics."""

from __future__ import annotations

import _paths  # noqa: F401

from contact_event_lifecycle import build_events, due_losses
from contact_state_machine import ContactLifecycle
from p5_t4_sim_env import FeasibilityGraspEnv
from p5_t4r2_experiment_protocol import PERSIST_STEPS
from persistent_contact_manager import PersistentContactManager


class LifecycleGraspEnv(FeasibilityGraspEnv):
    def __init__(self, scenario, protocol, dt: float = 0.005, semantics: str = "oneshot", finger: str = "physical"):
        self.semantics = semantics
        self.finger_mode = finger
        super().__init__(scenario, protocol, dt)

    def reset(self):
        obs = super().reset()
        self.events = build_events(self.scenario)
        self.lifecycle = ContactLifecycle(len(self.template))
        self.manager = PersistentContactManager()
        self.loss_calls = 0
        self.loss_applications = 0
        return obs

    def _lose(self, idx: int):
        self.loss_calls += 1
        already = idx in self.lost
        super()._lose(idx)
        if not already and idx in self.lost:
            self.loss_applications += 1

    def _event(self):
        if self.semantics == "repeat":
            super()._event()
            return
        scenario = self.scenario
        kind = scenario["event"]
        t = self.time
        t0 = float(scenario["event_time"])
        if kind == "friction_collapse" and t >= t0:
            for idx in scenario["lost_ids"]:
                self.template[int(idx)].mu = float(self.base_mu[int(idx)] * float(scenario["collapse_factor"]))
        elif kind == "gradual":
            if t0 <= t <= t0 + float(scenario["event_duration"]):
                alpha = (t - t0) / max(1e-6, float(scenario["event_duration"]))
                for idx in scenario["lost_ids"]:
                    self.template[int(idx)].mu = float(self.base_mu[int(idx)] * (1.0 - 0.75 * alpha))
            elif t > t0 + float(scenario["event_duration"]):
                for idx in scenario["lost_ids"]:
                    self.template[int(idx)].mu = float(self.base_mu[int(idx)] * 0.25)
        for event in due_losses(self.events, t, self.semantics):
            index = int(event["contact_id"])
            if event["event_type"] == "stiffness_change" and not event.get("stiffness_applied"):
                self.template[index].stiffness = float(scenario["stiffness"][index] * float(scenario["stiffness_factor"]))
                event["stiffness_applied"] = True
            self._lose(index)
            event["trigger_time"] = t
            if not event["persistent"]:
                event["consumed"] = True
            permanent = bool(event["persistent"])
            self.lifecycle.mark_loss(index, permanent)

    def _obstructed(self, index: int) -> bool:
        for event in self.events:
            if int(event["contact_id"]) == int(index) and event["persistent"]:
                if self.time + 1e-12 >= float(event["scheduled_time"]):
                    return True
        return False

    def _try_recontact(self, requests):
        if self.finger_mode == "original":
            super()._try_recontact(requests)
            return
        for index, want in enumerate(requests):
            if not want or index not in self.lost or self._obstructed(index):
                continue
            self.lifecycle.mark_approach(index)
            stage = self.manager.approach(self, index)
            if stage == "forming":
                self.lifecycle.mark_forming(index)

    def step(self, command):
        obs, info = super().step(command)
        self.manager.account(self)
        for index, steps in self.manager.loaded_steps.items():
            self.lifecycle.mark_loaded(index, steps, PERSIST_STEPS)
        info["loss_calls"] = int(self.loss_calls)
        info["loss_applications"] = int(self.loss_applications)
        info["formations"] = int(self.manager.formations)
        info["verified_contacts"] = int(self.manager.verified)
        info["max_persist"] = int(max(self.manager.loaded_steps.values(), default=0))
        info["events_consumed"] = int(sum(1 for event in self.events if event["consumed"]))
        info["event_count"] = len(self.events)
        info["lifecycle"] = self.lifecycle.snapshot()
        info["verify_steps"] = PERSIST_STEPS
        return obs, info
