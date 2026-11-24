"""Draw archived scenarios, then attach preregistered event ids before labeling."""

from __future__ import annotations

import _paths  # noqa: F401
from typing import Dict, List, Optional

from contact_loss_scenario_generator import _draw, _label


def _annotate(scenario: Dict[str, object]) -> Optional[Dict[str, object]]:
    seed = int(scenario["seed"])
    if scenario["event"] == "sudden_invalidation" and seed % 23 == 0:
        scenario["event"] = "persistent_obstruction"
        scenario["obstruction_end"] = float(scenario["horizon_steps"]) * 0.005
    extra = []
    if seed % 19 == 0 and int(scenario["num_contacts"]) >= 4 and scenario["event"] in (
        "sudden_invalidation", "multi_loss", "object_motion", "stiffness_change", "persistent_obstruction",
    ):
        used = {int(i) for i in scenario["lost_ids"]}
        for index in range(int(scenario["num_contacts"])):
            if index not in used:
                extra.append({
                    "event_id": f"second_loss:{index}:0.220",
                    "contact_id": index,
                    "event_type": "second_loss",
                    "scheduled_time": 0.22,
                    "persistent": False,
                })
                break
    scenario["extra_events"] = extra
    saved_ids = list(scenario["lost_ids"])
    saved_event = scenario["event"]
    scenario["lost_ids"] = saved_ids + [int(item["contact_id"]) for item in extra]
    if saved_event == "persistent_obstruction":
        scenario["event"] = "sudden_invalidation"
    label_a = _label(scenario, "Protocol_A")
    label_b = _label(scenario, "Protocol_B")
    scenario["event"] = saved_event
    scenario["lost_ids"] = saved_ids
    if not label_a["initial_grasp_feasible"]:
        return None
    scenario["oracle_protocol_a"] = bool(label_a["bounded_feasible"])
    scenario["oracle_protocol_b"] = bool(label_b["bounded_feasible"])
    scenario["n_active_after_event"] = label_a["n_active_after_event"]
    scenario["recontact_oracle_possible"] = scenario["event"] != "persistent_obstruction"
    scenario["group"] = "feasible" if scenario["oracle_protocol_a"] else "infeasible"
    return scenario


def draw_seed(seed: int, num_contacts: int, split: str) -> Optional[Dict[str, object]]:
    scenario = _draw(seed, num_contacts, split)
    if scenario is None:
        return None
    return _annotate(scenario)


def generate_partition(split: str, seed_start: int, seed_stop: int, per_topology: Dict[int, Dict[str, int]]) -> Dict[str, object]:
    accepted: List[Dict[str, object]] = []
    shortfall = {}
    next_seed = int(seed_start)
    for n, quota in per_topology.items():
        need = {key: int(quota[key]) for key in ("feasible", "infeasible", "extra")}
        got = {key: 0 for key in need}
        while sum(got.values()) < sum(need.values()) and next_seed < seed_stop:
            scenario = draw_seed(next_seed, n, split)
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
    return {"split": split, "seed_start": seed_start, "seed_stop_exclusive_used": next_seed, "scenarios": accepted, "shortfall": shortfall}
