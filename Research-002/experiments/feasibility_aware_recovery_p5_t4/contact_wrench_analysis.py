"""
Planar contact wrench algebra for P5-T4.

Force on the object at contact i is fn * normal + ft * tangent.
The normal is the inward direction. Torque is about the center of mass:
tau = rx * fy - ry * fx.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

MU_CRIT_SYMMETRIC_PAIR = 1.0 / math.sqrt(3.0)


@dataclass
class ContactState:
    contact_id: int
    position: np.ndarray
    normal: np.ndarray
    tangent: np.ndarray
    mu: float
    stiffness: float = 4.0e4
    fn: float = 0.0
    ft: float = 0.0
    valid: bool = True
    displacement: float = 0.0
    mu_sigma: float = 0.04

    def copy(self) -> "ContactState":
        return ContactState(
            contact_id=self.contact_id,
            position=np.array(self.position, dtype=float).copy(),
            normal=np.array(self.normal, dtype=float).copy(),
            tangent=np.array(self.tangent, dtype=float).copy(),
            mu=float(self.mu),
            stiffness=float(self.stiffness),
            fn=float(self.fn),
            ft=float(self.ft),
            valid=bool(self.valid),
            displacement=float(self.displacement),
            mu_sigma=float(self.mu_sigma),
        )


@dataclass
class ObjectState:
    position: np.ndarray = field(default_factory=lambda: np.zeros(2))
    theta: float = 0.0
    linear_velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    angular_velocity: float = 0.0
    mass: float = 0.40
    inertia: float = 3.2e-4
    com_offset: np.ndarray = field(default_factory=lambda: np.zeros(2))
    external_force: np.ndarray = field(default_factory=lambda: np.zeros(2))
    external_torque: float = 0.0
    radius: float = 0.040


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise ValueError("zero-length vector cannot be normalized")
    return np.asarray(v, dtype=float) / n


def contact_force(contact: ContactState, fn: float, ft: float) -> np.ndarray:
    return float(fn) * contact.normal + float(ft) * contact.tangent


def contact_wrench(contact: ContactState, fn: float, ft: float) -> np.ndarray:
    force = contact_force(contact, fn, ft)
    tau = float(contact.position[0] * force[1] - contact.position[1] * force[0])
    return np.array([force[0], force[1], tau], dtype=float)


def total_contact_wrench(contacts: Sequence[ContactState], fn: Sequence[float], ft: Sequence[float]) -> np.ndarray:
    total = np.zeros(3)
    for contact, f_n, f_t in zip(contacts, fn, ft):
        if not contact.valid:
            continue
        total += contact_wrench(contact, f_n, f_t)
    return total


def external_wrench(obj: ObjectState, include_gravity: bool = True, gravity: float = 9.81) -> np.ndarray:
    force = np.array(obj.external_force, dtype=float).copy()
    if include_gravity:
        force = force + np.array([0.0, -obj.mass * gravity])
    return np.array([force[0], force[1], float(obj.external_torque)], dtype=float)


def equilibrium_residual(
    contacts: Sequence[ContactState],
    fn: Sequence[float],
    ft: Sequence[float],
    w_ext: np.ndarray,
) -> np.ndarray:
    """Contact wrench plus external wrench. Zero is equilibrium."""
    return total_contact_wrench(contacts, fn, ft) + np.asarray(w_ext, dtype=float)


def symmetric_pair_horizontal_margin(mu_1: float, mu_2: float, fn_1: float, fn_2: float) -> float:
    """
    Opposing horizontal capacity minus the outward normal component for the
    P5-T3 120 deg / 240 deg pair, using force magnitudes.

    (sqrt(3)/2) * (mu1 fn1 + mu2 fn2) - (1/2) * (fn1 + fn2)
    """
    capacity = (math.sqrt(3.0) / 2.0) * (mu_1 * fn_1 + mu_2 * fn_2)
    demand = 0.5 * (fn_1 + fn_2)
    return capacity - demand


def make_radial_contact(
    contact_id: int,
    angle_rad: float,
    radius: float,
    mu: float,
    stiffness: float,
    com_offset: np.ndarray,
) -> ContactState:
    center = np.array([radius * math.cos(angle_rad), radius * math.sin(angle_rad)])
    normal = unit(-center)
    tangent = np.array([-normal[1], normal[0]])
    return ContactState(
        contact_id=contact_id,
        position=center - np.asarray(com_offset, dtype=float),
        normal=normal,
        tangent=tangent,
        mu=float(mu),
        stiffness=float(stiffness),
    )


def rotate_contacts(contacts: Sequence[ContactState], theta: float) -> List[ContactState]:
    c = math.cos(theta)
    s = math.sin(theta)
    rot = np.array([[c, -s], [s, c]])
    rotated = []
    for contact in contacts:
        item = contact.copy()
        item.position = rot @ contact.position
        item.normal = rot @ contact.normal
        item.tangent = rot @ contact.tangent
        rotated.append(item)
    return rotated


def closest_friction(
    contacts: Sequence[ContactState],
    fn: Sequence[float],
    w_ext: np.ndarray,
    radius: float,
    iterations: int = 24,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Cone-limited tangential reactions closest to canceling w_ext at fixed normal forces.
    Returns (ft, residual wrench).
    """
    ft = np.zeros(len(contacts))
    w_ext = np.asarray(w_ext, dtype=float)
    scale = max(float(radius), 1e-4)
    for _ in range(iterations):
        resid = equilibrium_residual(contacts, fn, ft, w_ext)
        scaled = np.array([resid[0], resid[1], resid[2] / scale])
        for i, contact in enumerate(contacts):
            if not contact.valid or fn[i] <= 0.0:
                ft[i] = 0.0
                continue
            lever = (contact.position[0] * contact.tangent[1] - contact.position[1] * contact.tangent[0]) / scale
            grad = scaled[0] * contact.tangent[0] + scaled[1] * contact.tangent[1] + scaled[2] * lever
            ft[i] -= 0.65 * grad
            limit = abs(contact.mu) * float(fn[i])
            ft[i] = float(np.clip(ft[i], -limit, limit))
    resid = equilibrium_residual(contacts, fn, ft, w_ext)
    return ft, resid
