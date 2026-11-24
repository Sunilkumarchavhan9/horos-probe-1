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
            events.append(_record(
                f"{kind}:{int(index)}:{t0:.3f}", int(index),
                OBSTRUCTION if kind == OBSTRUCTION else kind, t0, kind == OBSTRUCTION,
            ))
    for extra in scenario.get("extra_events") or []:
        events.append(_record(
            str(extra["event_id"]), int(extra["contact_id"]), str(extra["event_type"]),
            float(extra["scheduled_time"]), bool(extra.get("persistent", False)),
        ))
    _link_subsequent(events)
    return events


def _record(event_id: str, contact_id: int, event_type: str, scheduled_time: float, persistent: bool) -> Dict[str, object]:
    return {
        "event_id": event_id,
        "contact_id": contact_id,
        "event_type": event_type,
        "scheduled_time": scheduled_time,
        "trigger_time": None,
        "consumed": False,
        "persistent": persistent,
        "contact_valid": True,
        "reestablishment_status": "not_attempted",
        "subsequent_event_ids": [],
    }


def _link_subsequent(events: List[Dict[str, object]]) -> None:
    by_contact: Dict[int, List[Dict[str, object]]] = {}
    for event in events:
        by_contact.setdefault(int(event["contact_id"]), []).append(event)
    for group in by_contact.values():
        group.sort(key=lambda event: float(event["scheduled_time"]))
        for earlier, later in zip(group, group[1:]):
            earlier["subsequent_event_ids"].append(later["event_id"])


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
