"""
Deterministic contact-loss scenarios.

The oracle label is the bounded-actuator linear program on the post-event
contacts. It is stored in the manifest and is not given to controllers.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

from contact_feasibility_solver import solve_bounded_wrench
from contact_wrench_analysis import external_wrench, make_radial_contact, ObjectState

EVENTS = (
    "sudden_invalidation",
    "multi_loss",
    "friction_collapse",
    "gradual",
    "stiffness_change",
    "object_motion",
)


def _post_event_contacts(scenario: Dict[str, object]):
    com = np.asarray(scenario["com_offset"], dtype=float)
    contacts = []
    for i in range(int(scenario["num_contacts"])):
        contact = make_radial_contact(
            i,
            float(scenario["angles_rad"][i]),
            float(scenario["radius"]),
            float(scenario["mu"][i]),
            float(scenario["stiffness"][i]),
            com,
        )
        contacts.append(contact)
    kind = scenario["event"]
    for idx in scenario["lost_ids"]:
        if kind in ("sudden_invalidation", "multi_loss", "stiffness_change", "object_motion"):
            contacts[int(idx)].valid = False
        elif kind == "friction_collapse":
            contacts[int(idx)].mu = float(scenario["mu"][int(idx)]) * float(scenario["collapse_factor"])
        elif kind == "gradual":
            contacts[int(idx)].mu = float(scenario["mu"][int(idx)]) * 0.25
    return contacts


def _label(scenario: Dict[str, object], protocol: str) -> Dict[str, object]:
    contacts = _post_event_contacts(scenario)
    obj = ObjectState(
        mass=float(scenario["mass"]),
        external_force=np.asarray(scenario["disturbance_force"], dtype=float),
        external_torque=float(scenario["disturbance_torque"]),
        radius=float(scenario["radius"]),
    )
    w = external_wrench(obj)
    active = sum(1 for c in contacts if c.valid)
    budget = None
    if protocol == "Protocol_B":
        budget = max(active, 1) * 8.0 if active else 0.0
    solved = solve_bounded_wrench(contacts, w, fn_min=0.0, fn_max=25.0, total_budget=budget)
    full = []
    com = np.asarray(scenario["com_offset"], dtype=float)
    for i in range(int(scenario["num_contacts"])):
        full.append(make_radial_contact(
            i, float(scenario["angles_rad"][i]), float(scenario["radius"]),
            float(scenario["mu"][i]), float(scenario["stiffness"][i]), com,
        ))
    full_solved = solve_bounded_wrench(full, w, fn_min=0.0, fn_max=25.0, total_budget=None)
    return {
        "bounded_feasible": bool(solved["feasible"]),
        "reason": solved["reason"],
        "sum_fn": solved["sum_fn"],
        "initial_grasp_feasible": bool(full_solved["feasible"]),
        "n_active_after_event": int(active),
        "residual_max": None if solved["residual"] is None else float(np.max(np.abs(solved["residual"]))),
    }


def _draw(seed: int, num_contacts: int, split: str) -> Optional[Dict[str, object]]:
    rng = np.random.RandomState(int(seed))
    if split == "test" or split == "stress":
        gaps = rng.uniform(0.45, 1.55, size=num_contacts)
        gaps = gaps / np.sum(gaps) * (2.0 * math.pi)
        angles = np.cumsum(gaps)
        angles = angles - angles[0]
    elif split == "validation":
        angles = np.linspace(0.0, 2.0 * math.pi, num_contacts, endpoint=False)
        angles = angles + rng.normal(0.0, math.radians(7.0), size=num_contacts)
    else:
        angles = np.linspace(0.0, 2.0 * math.pi, num_contacts, endpoint=False)

    hard = bool(rng.rand() < (0.55 if split != "stress" else 0.8))
    if split == "test":
        mu = rng.uniform(0.10, 0.36, size=num_contacts) if hard else rng.uniform(0.75, 1.15, size=num_contacts)
    elif split == "stress":
        mu = rng.uniform(0.08, 0.30, size=num_contacts) if hard else rng.uniform(0.85, 1.25, size=num_contacts)
    else:
        mu = rng.uniform(0.15, 0.45, size=num_contacts) if hard else rng.uniform(0.55, 0.90, size=num_contacts)
    if hard:
        mass = float(rng.uniform(0.45, 0.65))
        disturbance = rng.normal(0.0, 1.0, size=2)
        disturbance[0] += float(rng.choice([-1.0, 1.0])) * float(rng.uniform(2.0, 5.5))
        torque = float(rng.uniform(-0.08, 0.08))
        com = rng.uniform(-0.012, 0.012, size=2)
    else:
        mass = float(rng.uniform(0.25, 0.42))
        disturbance = rng.normal(0.0, 0.2, size=2)
        torque = float(rng.uniform(-0.01, 0.01))
        com = rng.uniform(-0.003, 0.003, size=2)
    event = EVENTS[int(seed) % len(EVENTS)]
    if event == "multi_loss":
        n_loss = int(min(num_contacts - 1, max(2, num_contacts - 2)))
    elif hard:
        n_loss = int(min(num_contacts - 1, max(1, num_contacts - 2)))
    else:
        n_loss = 1
    # Prefer to remove the contacts that push upward when the draw is hard.
    upward = []
    for i, angle in enumerate(angles):
        normal_y = -math.sin(float(angle))
        upward.append((normal_y, i))
    upward.sort(reverse=True)
    if hard:
        lost = [i for _, i in upward[:n_loss]]
    else:
        lost = [int(x) for x in rng.choice(num_contacts, size=n_loss, replace=False)]
    stiffness = rng.uniform(2.5e4, 6.0e4, size=num_contacts)
    radius = 0.040
    inertia = 0.5 * mass * radius * radius
    scenario = {
        "seed": int(seed),
        "split": split,
        "num_contacts": int(num_contacts),
        "angles_rad": [float(a) for a in angles],
        "radius": radius,
        "mu": [float(m) for m in mu],
        "stiffness": [float(k) for k in stiffness],
        "mass": mass,
        "inertia": float(inertia),
        "com_offset": [float(com[0]), float(com[1])],
        "disturbance_force": [float(disturbance[0]), float(disturbance[1])],
        "disturbance_torque": torque,
        "initial_velocity": [0.02, 0.0] if event == "object_motion" else [0.0, 0.0],
        "initial_omega": 0.4 if event == "object_motion" else 0.0,
        "event": event,
        "event_time": 0.04,
        "event_duration": 0.08,
        "lost_ids": lost,
        "collapse_factor": 0.2,
        "stiffness_factor": 0.4,
        "support_available": bool(rng.rand() < 0.45),
        "mu_noise": 0.05 if split != "stress" else 0.08,
        "angle_noise_rad": 0.02 if split != "stress" else 0.05,
        "horizon_steps": 140,
        "simulator": "p5_t4_isolated_planar_v1",
        "contact_physics": "isolated_coulomb_wrench_v1",
    }
    label_a = _label(scenario, "Protocol_A")
    if not label_a["initial_grasp_feasible"]:
        return None
    label_b = _label(scenario, "Protocol_B")
    scenario["oracle_protocol_a"] = bool(label_a["bounded_feasible"])
    scenario["oracle_protocol_b"] = bool(label_b["bounded_feasible"])
    scenario["oracle_reason_a"] = label_a["reason"]
    scenario["n_active_after_event"] = label_a["n_active_after_event"]
    scenario["group"] = "feasible" if scenario["oracle_protocol_a"] else "infeasible"
    return scenario


def generate_partition(
    split: str,
    seed_start: int,
    seed_stop: int,
    per_topology: Dict[int, Dict[str, int]],
) -> Dict[str, object]:
    accepted: List[Dict[str, object]] = []
    shortfall = {}
    next_seed = int(seed_start)
    for n, quota in per_topology.items():
        need = {"feasible": int(quota["feasible"]), "infeasible": int(quota["infeasible"]), "extra": int(quota.get("extra", 0))}
        got = {"feasible": 0, "infeasible": 0, "extra": 0}
        while sum(got.values()) < sum(need.values()) and next_seed < seed_stop:
            scenario = _draw(next_seed, n, split)
            next_seed += 1
            if scenario is None:
                continue
            kind = "feasible" if scenario["oracle_protocol_a"] else "infeasible"
            if got[kind] < need[kind]:
                scenario["quota_bin"] = kind
                accepted.append(scenario)
                got[kind] += 1
            elif got["feasible"] >= need["feasible"] and got["infeasible"] >= need["infeasible"] and got["extra"] < need["extra"]:
                scenario["quota_bin"] = "extra"
                accepted.append(scenario)
                got["extra"] += 1
        shortfall[str(n)] = {key: int(need[key] - got[key]) for key in need}
    return {
        "split": split,
        "seed_start": seed_start,
        "seed_stop_exclusive_used": next_seed,
        "scenarios": accepted,
        "shortfall": shortfall,
    }
