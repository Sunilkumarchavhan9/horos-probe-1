"""Event records. A one-shot id is consumed once. An obstruction stays in force."""

from __future__ import annotations

from typing import Dict, List


ONE_SHOT_LOSSES = ("sudden_invalidation", "multi_loss", "object_motion", "stiffness_change", "second_loss")
OBSTRUCTION = "persistent_obstruction"


def build_events(scenario: Dict[str, object]) -> List[Dict[str, object]]:
    events = []
    kind = str(scenario["event"])
    t0 = float(scenario["event_time"])
    if kind in ONE_SHOT_LOSSES or kind == OBSTRUCTION:
        for index in scenario["lost_ids"]:
            events.append({
                "event_id": f"{kind}:{int(index)}:{t0:.3f}",
                "contact_id": int(index),
                "event_type": kind if kind != OBSTRUCTION else OBSTRUCTION,
                "scheduled_time": t0,
                "trigger_time": None,
                "consumed": False,
                "persistent": kind == OBSTRUCTION,
            })
    for extra in scenario.get("extra_events") or []:
        events.append({
            "event_id": str(extra["event_id"]),
            "contact_id": int(extra["contact_id"]),
            "event_type": str(extra["event_type"]),
            "scheduled_time": float(extra["scheduled_time"]),
            "trigger_time": None,
            "consumed": False,
            "persistent": bool(extra.get("persistent", False)),
        })
    return events


def due_losses(events: List[Dict[str, object]], time: float, semantics: str) -> List[Dict[str, object]]:
    due = []
    for event in events:
        if time + 1e-12 < float(event["scheduled_time"]):
            continue
        if event["event_type"] not in ONE_SHOT_LOSSES and event["event_type"] != OBSTRUCTION:
            continue
        if semantics == "repeat":
            due.append(event)
            continue
        if event["persistent"]:
            due.append(event)
            continue
        if not event["consumed"]:
            due.append(event)
    return due
