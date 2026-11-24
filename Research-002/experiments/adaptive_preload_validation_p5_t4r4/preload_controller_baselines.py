"""
HOROS PROBE-1 — Phase 5 Task 4R4
Preload Controller Baselines
File: preload_controller_baselines.py

Implements all 10 compared controllers for the controlled preload benchmark:
1. Fixed 3 N
2. Fixed 5 N
3. Fixed 8 N
4. Fixed 12 N
5. Fixed 16 N
6. Fixed 20 N
7. Frozen preload_25 (25 N)
8. P5-T3 cooperative controller (dynamic squeeze)
9. P5-T4R2 topology_14 (14 N load-opposing allocation)
10. Proposed AdaptivePreloadPlanner
"""

from __future__ import annotations

from typing import Dict, Any, List, Optional
import numpy as np

import _paths  # noqa: F401
from preload_experiment_protocol import PROTOCOL_A, PROTOCOL_B
from adaptive_preload_planner import AdaptivePreloadPlanner
from preload_force_allocator import PreloadForceAllocator


def get_initial_preload(controller_name: str, protocol: str) -> float:
    """Returns the correct initial normal preload (N) for a controller under the given protocol."""
    if controller_name == "AdaptivePreloadPlanner":
        return 4.0
    if controller_name == "P5T3Cooperative":
        return 8.0
    if controller_name == "P5T4R2Topology14":
        return 8.0 if protocol == PROTOCOL_B else 14.0
    if controller_name == "preload_25":
        return 8.0 if protocol == PROTOCOL_B else 25.0
    if controller_name.startswith("Fixed_"):
        target = float(controller_name.split("_")[1].replace("N", ""))
        return min(target, 8.0) if protocol == PROTOCOL_B else target
    return 5.0


class FixedPreloadController:
    """Fixed-force normal clamping controller with slew-rate limiting."""
    def __init__(
        self,
        num_contacts: int,
        target_fn: float,
        name: Optional[str] = None,
        slew_limit: float = 50.0,
        dt: float = 0.001,
        initial_preload: Optional[float] = None,
    ):
        self.num_contacts = int(num_contacts)
        self.target_fn = float(target_fn)
        self.name = name or f"Fixed_{int(target_fn)}N"
        self.allocator = PreloadForceAllocator(slew_limit=slew_limit, dt=dt)
        self.initial_preload = float(initial_preload) if initial_preload is not None else self.target_fn
        self.reset()

    def reset(self, initial_fn: Optional[float] = None):
        val = float(initial_fn if initial_fn is not None else self.initial_preload)
        self.allocator.reset([val] * self.num_contacts)

    def step(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        valid = [bool(v) for v in obs.get("valid", [True] * self.num_contacts)]
        current_fn = [float(f) for f in obs.get("fn", [self.target_fn] * self.num_contacts)]
        normals = obs.get("normals", [[0.0, 1.0]] * self.num_contacts)
        positions = obs.get("positions", [[0.0, 0.0]] * self.num_contacts)
        w_ext_hat = obs.get("w_ext_hat", [0.0, 0.0, 0.0])
        budget = obs.get("protocol_budget")

        cmd = self.allocator.allocate(
            current_fn=current_fn,
            target_fn_per_contact=self.target_fn,
            normals=normals,
            positions=positions,
            valid=valid,
            w_ext_hat=w_ext_hat,
            budget=budget,
        )
        return {
            "cmd_fn": cmd.tolist(),
            "selected_preload": self.target_fn,
            "mode": "fixed",
        }


class P5T3CooperativeBaseline:
    """P5-T3 Cooperative Squeeze Controller."""
    name = "P5T3Cooperative"

    def __init__(self, num_contacts: int, slew_limit: float = 50.0, dt: float = 0.001, initial_preload: Optional[float] = None):
        self.num_contacts = int(num_contacts)
        self.allocator = PreloadForceAllocator(slew_limit=slew_limit, dt=dt)
        self.initial_preload = float(initial_preload) if initial_preload is not None else 8.0
        self.reset()

    def reset(self, initial_fn: Optional[float] = None):
        val = float(initial_fn if initial_fn is not None else self.initial_preload)
        self.allocator.reset([val] * self.num_contacts)

    def step(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        valid = [bool(v) for v in obs.get("valid", [True] * self.num_contacts)]
        current_fn = [float(f) for f in obs.get("fn", [8.0] * self.num_contacts)]
        active = sum(1 for v in valid if v)
        carried = float(sum(current_fn)) * 1.3
        share = float(np.clip(carried / max(1, active), 8.0, 25.0))

        normals = obs.get("normals", [[0.0, 1.0]] * self.num_contacts)
        positions = obs.get("positions", [[0.0, 0.0]] * self.num_contacts)
        w_ext_hat = obs.get("w_ext_hat", [0.0, 0.0, 0.0])
        budget = obs.get("protocol_budget")

        cmd = self.allocator.allocate(
            current_fn=current_fn,
            target_fn_per_contact=share,
            normals=normals,
            positions=positions,
            valid=valid,
            w_ext_hat=w_ext_hat,
            budget=budget,
        )
        return {
            "cmd_fn": cmd.tolist(),
            "selected_preload": share,
            "mode": "p5t3_cooperative",
        }


class P5T4R2Topology14Baseline:
    """P5-T4R2 Topology-14 Baseline (14 N load-opposing allocation)."""
    name = "P5T4R2Topology14"

    def __init__(self, num_contacts: int, slew_limit: float = 50.0, dt: float = 0.001, initial_preload: Optional[float] = None):
        self.num_contacts = int(num_contacts)
        self.allocator = PreloadForceAllocator(slew_limit=slew_limit, dt=dt)
        self.initial_preload = float(initial_preload) if initial_preload is not None else 14.0
        self.reset()

    def reset(self, initial_fn: Optional[float] = None):
        val = float(initial_fn if initial_fn is not None else self.initial_preload)
        self.allocator.reset([val] * self.num_contacts)

    def step(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        valid = [bool(v) for v in obs.get("valid", [True] * self.num_contacts)]
        current_fn = [float(f) for f in obs.get("fn", [14.0] * self.num_contacts)]
        normals = obs.get("normals", [[0.0, 1.0]] * self.num_contacts)
        positions = obs.get("positions", [[0.0, 0.0]] * self.num_contacts)
        w_ext_hat = obs.get("w_ext_hat", [0.0, 0.0, 0.0])
        budget = obs.get("protocol_budget")

        cmd = self.allocator.allocate(
            current_fn=current_fn,
            target_fn_per_contact=14.0,
            normals=normals,
            positions=positions,
            valid=valid,
            w_ext_hat=w_ext_hat,
            budget=budget,
        )
        return {
            "cmd_fn": cmd.tolist(),
            "selected_preload": 14.0,
            "mode": "topology_14",
        }


def make_controller(name: str, num_contacts: int, seed: int = 4930001, initial_preload: Optional[float] = None):
    if name == "Fixed_3N":
        return FixedPreloadController(num_contacts, 3.0, name="Fixed_3N", initial_preload=initial_preload)
    if name == "Fixed_5N":
        return FixedPreloadController(num_contacts, 5.0, name="Fixed_5N", initial_preload=initial_preload)
    if name == "Fixed_8N":
        return FixedPreloadController(num_contacts, 8.0, name="Fixed_8N", initial_preload=initial_preload)
    if name == "Fixed_12N":
        return FixedPreloadController(num_contacts, 12.0, name="Fixed_12N", initial_preload=initial_preload)
    if name == "Fixed_16N":
        return FixedPreloadController(num_contacts, 16.0, name="Fixed_16N", initial_preload=initial_preload)
    if name == "Fixed_20N":
        return FixedPreloadController(num_contacts, 20.0, name="Fixed_20N", initial_preload=initial_preload)
    if name == "preload_25":
        return FixedPreloadController(num_contacts, 25.0, name="preload_25", initial_preload=initial_preload)
    if name == "P5T3Cooperative":
        return P5T3CooperativeBaseline(num_contacts, initial_preload=initial_preload)
    if name == "P5T4R2Topology14":
        return P5T4R2Topology14Baseline(num_contacts, initial_preload=initial_preload)
    if name == "AdaptivePreloadPlanner":
        return AdaptivePreloadPlanner(num_contacts, seed=seed)
    raise ValueError(f"Unknown controller: {name}")
