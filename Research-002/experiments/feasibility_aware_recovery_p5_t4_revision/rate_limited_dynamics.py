"""Rate-limited rollout using the archived contact reaction and integrator."""

from __future__ import annotations

import _paths  # noqa: F401
from typing import Dict, Sequence

import numpy as np

from contact_feasibility_solver import reaction_friction
from contact_wrench_analysis import ContactState

SLEW = 50.0
FN_MAX = 25.0
DROP_M = 0.025
TILT_LIMIT = 0.3490658503988659
LINEAR_DAMPING = 0.2
ANGULAR_DAMPING = 0.001


def slew_toward(fn: np.ndarray, target: np.ndarray, valid: Sequence[bool], dt: float) -> np.ndarray:
    out = np.array(fn, dtype=float).copy()
    for i, is_valid in enumerate(valid):
        if not is_valid:
            out[i] = 0.0
            continue
        raw = float(np.clip(target[i], 0.0, FN_MAX))
        delta = float(np.clip(raw - out[i], -SLEW * dt, SLEW * dt))
        out[i] = float(np.clip(out[i] + delta, 0.0, FN_MAX))
    return out


def max_slew_gap(fn: np.ndarray, target: np.ndarray, valid: Sequence[bool]) -> float:
    gap = 0.0
    for i, is_valid in enumerate(valid):
        if is_valid:
            gap = max(gap, abs(float(target[i]) - float(fn[i])))
    return gap


def _contacts(normals, tangents, positions, mu, valid) -> list:
    built = []
    for i in range(len(valid)):
        built.append(ContactState(
            contact_id=i,
            position=np.asarray(positions[i], dtype=float),
            normal=np.asarray(normals[i], dtype=float),
            tangent=np.asarray(tangents[i], dtype=float),
            mu=float(mu[i]),
            valid=bool(valid[i]),
        ))
    return built


def rollout(
    fn: Sequence[float],
    target: Sequence[float],
    normals,
    tangents,
    positions,
    mu,
    valid: Sequence[bool],
    w_ext: Sequence[float],
    position: Sequence[float],
    velocity: Sequence[float],
    theta: float,
    omega: float,
    mass: float,
    inertia: float,
    dt: float,
    steps: int,
    mu_scale: float = 1.0,
    wrench_scale: float = 1.0,
) -> Dict[str, object]:
    """Integrate the archived residual dynamics. Geometry is held in the current object frame."""
    force = np.asarray(fn, dtype=float).copy()
    command = np.asarray(target, dtype=float)
    pos = np.asarray(position, dtype=float).copy()
    vel = np.asarray(velocity, dtype=float).copy()
    ang = float(theta)
    om = float(omega)
    external = np.asarray(w_ext, dtype=float) * float(wrench_scale)
    scaled_mu = [max(0.02, float(m) * mu_scale) for m in mu]
    contacts = _contacts(normals, tangents, positions, scaled_mu, valid)
    peak_disp = float(np.linalg.norm(pos))
    crossed = None
    for step in range(int(steps)):
        force = slew_toward(force, command, valid, dt)
        for i, contact in enumerate(contacts):
            contact.fn = float(force[i])
            contact.valid = bool(valid[i]) and force[i] > 0.0
        reaction = reaction_friction(contacts, force, external)
        residual = np.asarray(reaction["residual"], dtype=float)
        net_force = residual[:2] - LINEAR_DAMPING * vel
        net_tau = float(residual[2]) - ANGULAR_DAMPING * om
        vel = vel + dt * (net_force / mass)
        om = om + dt * (net_tau / inertia)
        pos = pos + dt * vel
        ang = ang + dt * om
        disp = float(np.linalg.norm(pos))
        peak_disp = max(peak_disp, disp)
        if crossed is None and (disp >= DROP_M or abs(ang) >= TILT_LIMIT):
            crossed = step + 1
            break
    return {
        "survived": crossed is None and peak_disp < DROP_M and abs(ang) < TILT_LIMIT,
        "steps_to_boundary": int(steps if crossed is None else crossed),
        "peak_disp_m": peak_disp,
        "final_speed": float(np.linalg.norm(vel)),
        "final_fn": force,
    }
