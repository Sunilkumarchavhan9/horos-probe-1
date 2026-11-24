"""Frozen corrective-continuation constants. Selection happens before held-out scoring."""

from __future__ import annotations

PROTOCOL_A = "Protocol_A"
PROTOCOL_B = "Protocol_B"

TRAIN_RANGE = (4850001, 4852501)
VAL_RANGE = (4860001, 4862501)
TEST_RANGE = (4870001, 4876001)
STRESS_RANGE = (4880001, 4881501)

PRIMARY_BASELINE = "P5T3RecoveryNoFeasibility"
ARCHIVED_CONTROLLER = "P5T4FeasibilityAwareRecovery"

SELECTION_MODES = ("preload_12", "preload_16", "preload_25")

CONTROLLERS = (
    "P5T3RecoveryNoFeasibility",
    "P5T4FeasibilityAwareRecovery",
    "P5T4StaticFeasibilityRecovery",
    "P5T4RateLimitedDynamic",
    "P5T4PredictiveRecontact",
    "P5T4CorrectiveRecovery",
    "P5T4SafeDegradation",
)

PROPOSED = "P5T4CorrectiveRecovery"

TRAIN_QUOTA = {3: {"feasible": 24, "infeasible": 24, "extra": 12}, 4: {"feasible": 24, "infeasible": 24, "extra": 12}, 5: {"feasible": 24, "infeasible": 24, "extra": 12}}
VAL_QUOTA = {3: {"feasible": 16, "infeasible": 16, "extra": 8}, 4: {"feasible": 16, "infeasible": 16, "extra": 8}, 5: {"feasible": 16, "infeasible": 16, "extra": 8}}
TEST_QUOTA = {3: {"feasible": 100, "infeasible": 100, "extra": 100}, 4: {"feasible": 100, "infeasible": 100, "extra": 100}, 5: {"feasible": 100, "infeasible": 100, "extra": 100}}
STRESS_QUOTA = {3: {"feasible": 12, "infeasible": 12, "extra": 6}, 4: {"feasible": 12, "infeasible": 12, "extra": 6}, 5: {"feasible": 12, "infeasible": 12, "extra": 6}}

COMPARATORS = tuple(name for name in CONTROLLERS if name != PROPOSED)
BONFERRONI = 0.05 / len(COMPARATORS)
