"""A closed gap is not yet a loaded contact."""

from __future__ import annotations

from p5_t4r2_experiment_protocol import FORM_FN, FORM_GAP_M


def gap_closed(gap_m: float) -> bool:
    return float(gap_m) <= FORM_GAP_M


def approach_safe(closing_speed: float, finger_speed: float) -> bool:
    return abs(float(closing_speed)) < max(0.08, float(finger_speed))


def force_developed(normal_force: float) -> bool:
    return float(normal_force) >= FORM_FN


def formation_stage(gap_m: float, closing_speed: float, normal_force: float, finger_speed: float) -> str:
    if not gap_closed(gap_m):
        return "approaching"
    if not approach_safe(closing_speed, finger_speed):
        return "rejected_approach_speed"
    if not force_developed(normal_force):
        return "forming"
    return "loaded"
