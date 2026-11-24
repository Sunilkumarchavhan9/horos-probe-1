"""HOROS World Model vNext (HCTT-256) package.

Implements the Stage 3.4 History-Conditioned Temporal Transformer (HCTT-256),
temporal sliding-window dataset, separate audit-target loader, episode split
manifest builder, locked multi-objective losses, read-only evaluation, and
authorization-locked training runner.
"""

from .contracts import (
    BENCHMARK_ACCEPTANCE_TARGETS,
    ContractViolationError,
    FORBIDDEN_MODEL_INPUT_KEYS,
    HCTTArchitectureConfig,
    LOCKED_HCTT_CONFIG,
    MODEL_VISIBLE_NPZ_KEYS,
    OPERATIONAL_MODE_NAMES,
    SUPPORTED_OPERATIONAL_MODE_IDS,
    TASK_INTERFACES,
    TaskInterfaceSpec,
    assert_no_privileged_keys_in_inputs,
    get_task_interface,
)
from .dataset import (
    EpisodeArrayRecord,
    HCTTTemporalDataset,
    WindowSampleIndex,
    collate_hctt_batch,
    load_clean_training_episode,
    split_batch_inputs_and_targets,
)
from .evaluator import HCTTReadOnlyEvaluator
from .hctt import (
    HCTTWorldModel,
    RotaryPositionEmbedding,
    build_causal_history_future_mask,
    extract_observable_contact_signals,
)
from .losses import HCTTMultiObjectiveLoss
from .runner import (
    EXECUTION_AUTHORIZED,
    HCTTTrainingRunner,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
)
from .splits import (
    PROTECTED_HISTORICAL_SEED_RANGES,
    build_episode_split_manifest,
    verify_split_manifest_disjointness,
)
from .target_loader import AuditEpisodeTargets, AuditTargetLoader

__all__ = [
    "AuditEpisodeTargets",
    "AuditTargetLoader",
    "BENCHMARK_ACCEPTANCE_TARGETS",
    "ContractViolationError",
    "EXECUTION_AUTHORIZED",
    "EpisodeArrayRecord",
    "FORBIDDEN_MODEL_INPUT_KEYS",
    "HCTTArchitectureConfig",
    "HCTTMultiObjectiveLoss",
    "HCTTReadOnlyEvaluator",
    "HCTTTemporalDataset",
    "HCTTTrainingRunner",
    "HCTTWorldModel",
    "LOCKED_HCTT_CONFIG",
    "MODEL_VISIBLE_NPZ_KEYS",
    "OPERATIONAL_MODE_NAMES",
    "PROTECTED_HISTORICAL_SEED_RANGES",
    "RotaryPositionEmbedding",
    "SUPPORTED_OPERATIONAL_MODE_IDS",
    "TASK_INTERFACES",
    "TRAINING_PERMITTED",
    "TaskInterfaceSpec",
    "TrainingAuthorizationError",
    "WindowSampleIndex",
    "assert_no_privileged_keys_in_inputs",
    "build_causal_history_future_mask",
    "build_episode_split_manifest",
    "collate_hctt_batch",
    "extract_observable_contact_signals",
    "get_task_interface",
    "load_clean_training_episode",
    "split_batch_inputs_and_targets",
    "verify_split_manifest_disjointness",
]
