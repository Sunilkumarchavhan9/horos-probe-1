"""Path helper for P5-T4R4 Adaptive Preload Policy Validation."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parent
RESEARCH = EXPERIMENTS.parent

paths = [
    HERE,
    EXPERIMENTS / "contact_physics_v2",
    EXPERIMENTS / "n_contact_generalization_p5_t3",
    EXPERIMENTS / "persistent_contact_recovery_p5_t4r2",
    EXPERIMENTS / "feasibility_aware_recovery_p5_t4_revision",
    EXPERIMENTS / "feasibility_aware_recovery_p5_t4",
    EXPERIMENTS / "reflexive_control_p4_t4b",
    RESEARCH,
]

# Ensure HERE is always at sys.path[0]
for p in reversed(paths):
    ps = str(p)
    if ps in sys.path:
        sys.path.remove(ps)
    sys.path.insert(0, ps)
