"""Frozen protocol constants. Threshold search ranges are part of the specification."""

from __future__ import annotations

PROTOCOL_A = "Protocol_A"
PROTOCOL_B = "Protocol_B"

TRAIN_RANGE = (4810001, 4810900)
VAL_RANGE = (4820001, 4820900)
TEST_RANGE = (4830001, 4834000)
STRESS_RANGE = (4840001, 4840600)

RESIDUAL_CANDIDATES = ("none", "boost_best_mu", "oppose_load")
CUTOFF_GRID = ((0.20, 0.65), (0.20, 0.80), (0.20, 0.90), (0.35, 0.65), (0.35, 0.80), (0.35, 0.90))

CONTROLLER_NAMES = (
    "EqualForcePerContact",
    "FixedTotalForce",
    "RuleBasedCooperative",
    "PredictiveMarginBalancing",
    "IndependentP5T1Adaptive",
    "P5T3LearnedUncertaintyAware",
    "P5T3RecoveryNoFeasibility",
    "P5T4FeasibilityAwareRecovery",
    "P5T4SafeDegradation",
)

PROPOSED = "P5T4FeasibilityAwareRecovery"

# Quotas are targets. The generator records a shortfall instead of relabeling.
TRAIN_QUOTA = {3: {"feasible": 24, "infeasible": 24, "extra": 12}, 4: {"feasible": 24, "infeasible": 24, "extra": 12}, 5: {"feasible": 24, "infeasible": 24, "extra": 12}}
VAL_QUOTA = {3: {"feasible": 16, "infeasible": 16, "extra": 8}, 4: {"feasible": 16, "infeasible": 16, "extra": 8}, 5: {"feasible": 16, "infeasible": 16, "extra": 8}}
TEST_QUOTA = {3: {"feasible": 100, "infeasible": 100, "extra": 100}, 4: {"feasible": 100, "infeasible": 100, "extra": 100}, 5: {"feasible": 100, "infeasible": 100, "extra": 100}}
STRESS_QUOTA = {3: {"feasible": 12, "infeasible": 12, "extra": 6}, 4: {"feasible": 12, "infeasible": 12, "extra": 6}, 5: {"feasible": 12, "infeasible": 12, "extra": 6}}
