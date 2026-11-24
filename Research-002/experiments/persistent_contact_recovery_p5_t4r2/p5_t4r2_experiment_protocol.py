"""Preregistered P5-T4R2 constants. Held-out scoring starts only after this file is frozen."""

from __future__ import annotations

PROTOCOL_A = "Protocol_A"
PROTOCOL_B = "Protocol_B"

# These ranges sit after P5-T4 4810001–4840599 and the corrective run 4850001–4881501.
TRAIN_RANGE = (4890001, 4892501)
VAL_RANGE = (4900001, 4902501)
TEST_RANGE = (4910001, 4916001)
STRESS_RANGE = (4920001, 4921501)

SELECTION_MODES = ("topology_10", "topology_14")
PROTECTED_RANGES = (
    (4400001, 4400300),
    (4500001, 4500300),
    (4600001, 4600300),
    (4700001, 4700900),
    (4710001, 4710900),
    (4720001, 4720300),
    (4810001, 4840600),
    (4850001, 4881501),
)

PLANT_DT = 0.005
SLEW = 50.0
FN_MAX = 25.0
PERSIST_STEPS = 100
FINGER_SPEED = 0.18
RETRACT_M = 0.010
FORM_GAP_M = 0.0015
FORM_FN = 0.30

TRAIN_QUOTA = {3: {"feasible": 16, "infeasible": 16, "extra": 8}, 4: {"feasible": 16, "infeasible": 16, "extra": 8}, 5: {"feasible": 16, "infeasible": 16, "extra": 8}}
VAL_QUOTA = {3: {"feasible": 12, "infeasible": 12, "extra": 6}, 4: {"feasible": 12, "infeasible": 12, "extra": 6}, 5: {"feasible": 12, "infeasible": 12, "extra": 6}}
TEST_QUOTA = {3: {"feasible": 80, "infeasible": 80, "extra": 40}, 4: {"feasible": 80, "infeasible": 80, "extra": 40}, 5: {"feasible": 80, "infeasible": 80, "extra": 40}}
STRESS_QUOTA = {3: {"feasible": 8, "infeasible": 8, "extra": 4}, 4: {"feasible": 8, "infeasible": 8, "extra": 4}, 5: {"feasible": 8, "infeasible": 8, "extra": 4}}
