"""Locked contracts, dimensions, and security boundaries for HOROS World Model vNext (HCTT-256).

Authoritative references:
- manifests/horos_world_model_vnext_manifest.json (schema horos_world_model_vnext_manifest_v1.1)
- experiments/world_model_vnext/horos_vnext_locked_training_spec.md (HOROS-P1-R002-SPEC-LOCKED-001)
- experiments/world_model_vnext/horos_world_model_vnext_spec.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple


@dataclass(frozen=True)
class TaskInterfaceSpec:
    """Immutable physical input/output interface for one robotic interaction task."""

    name: str
    task_index: int
    state_dim: int
    action_dim: int
    wrench_dim: int
    canonical_episode_length: int
    dt_s: float = 0.01


@dataclass(frozen=True)
class HCTTArchitectureConfig:
    """Frozen HCTT-256 architectural hyperparameters (HOROS-P1-R002-SPEC-LOCKED-001)."""

    model_family: str = "History-Conditioned Temporal Transformer (HCTT)"
    model_identifier: str = "HOROS-WM-vNext-alpha"
    d_model: int = 256
    num_layers: int = 6
    num_heads: int = 8
    d_head: int = 32
    ffn_mult: int = 4
    dropout: float = 0.1
    history_horizon: int = 10
    prediction_horizons: Tuple[int, ...] = (1, 3, 5, 10, 20)
    max_future_horizon: int = 20
    num_operational_modes: int = 5
    dt_s: float = 0.01
    rope_theta: float = 10000.0
    focal_gamma: float = 2.0

    # Locked loss weights: L_total = 1.0*L_sim + 0.5*L_mode + 0.25*L_sysid + 0.10*L_distill
    weight_sim_dynamics: float = 1.0
    weight_mode_focal: float = 0.5
    weight_sys_id: float = 0.25
    weight_distill: float = 0.10

    def __post_init__(self) -> None:
        if self.d_model != self.num_heads * self.d_head:
            raise ValueError(
                f"d_model ({self.d_model}) must equal num_heads * d_head ({self.num_heads} * {self.d_head})"
            )


LOCKED_HCTT_CONFIG = HCTTArchitectureConfig()

TASK_INTERFACES: Dict[str, TaskInterfaceSpec] = {
    "compliant_prelift": TaskInterfaceSpec(
        name="compliant_prelift",
        task_index=0,
        state_dim=23,
        action_dim=5,
        wrench_dim=6,
        canonical_episode_length=20,
        dt_s=0.01,
    ),
    "peg_in_hole": TaskInterfaceSpec(
        name="peg_in_hole",
        task_index=1,
        state_dim=15,
        action_dim=6,
        wrench_dim=6,
        canonical_episode_length=30,
        dt_s=0.01,
    ),
}

# The five operational contact modes present in Stage 3.3 production datasets and locked spec.
# Note: ContactMode.JAMMING (5) and ContactMode.RELEASE (6) from the 7-class ContactMode enum
# have 0 examples in the Stage 3.3 production dataset and are NOT predicted by the 5-mode head.
OPERATIONAL_MODE_NAMES: Dict[int, str] = {
    0: "Free",
    1: "FirstContact",
    2: "Sticking",
    3: "Sliding",
    4: "Impact",
}
SUPPORTED_OPERATIONAL_MODE_IDS: FrozenSet[int] = frozenset(OPERATIONAL_MODE_NAMES.keys())
UNSUPPORTED_DATASET_MODE_IDS: FrozenSet[int] = frozenset({5, 6})  # Jamming, Release

# Strict allowlist for model-visible NPZ fields
MODEL_VISIBLE_NPZ_KEYS: FrozenSet[str] = frozenset(
    {"states", "actions", "next_states", "wrenches", "next_wrenches", "sample_id", "task"}
)

# Keys that MUST NEVER be present in model inference inputs
FORBIDDEN_MODEL_INPUT_KEYS: FrozenSet[str] = frozenset(
    {
        "modes",
        "target_modes",
        "target_mu",
        "target_mass",
        "friction_mu",
        "mu_true",
        "initial_mu_true",
        "step_mu_true",
        "mass_kg",
        "object_mass",
        "initial_mass_kg",
        "step_object_mass_kg",
        "clearance_mm",
        "clearance",
        "step_contact_stratum",
        "contact_mode",
        "contactmode",
        "seed",
        "episode_id",
        "canonical_episode_id",
        "slot_alias",
        "provenance_hash",
        "rewards",
        "env_metadata",
    }
)

# Locked pre-training benchmark acceptance thresholds (future targets after authorized training)
BENCHMARK_ACCEPTANCE_TARGETS: Dict[str, float] = {
    "multi_horizon_mse_reduction_min": 0.40,
    "contact_mode_macro_f1_min": 0.85,
    "latent_friction_r2_min": 0.60,
    "latent_mass_r2_min": 0.60,
    "ood_discrimination_auroc_min": 0.85,
    "conformal_coverage_min": 0.90,
}


class ContractViolationError(ValueError):
    """Raised on any schema mismatch, unauthorized training call, or privileged-input leakage."""


def get_task_interface(task: str) -> TaskInterfaceSpec:
    """Returns the locked TaskInterfaceSpec or raises ContractViolationError."""
    if task not in TASK_INTERFACES:
        raise ContractViolationError(
            f"Unsupported task '{task}'. Locked interfaces support only {sorted(TASK_INTERFACES.keys())}."
        )
    return TASK_INTERFACES[task]


def assert_no_privileged_keys_in_inputs(inputs: Dict[str, object]) -> None:
    """Rejects any inference dictionary containing privileged or target keys."""
    for key in inputs.keys():
        lowered = str(key).lower()
        if lowered in FORBIDDEN_MODEL_INPUT_KEYS:
            raise ContractViolationError(
                f"Privileged or target key '{key}' is strictly forbidden in model inference inputs."
            )
        for token in (
            "mu_true",
            "mass_kg",
            "clearance",
            "contact_mode",
            "step_mu",
            "step_object_mass",
            "step_contact_stratum",
            "privileged",
        ):
            if token in lowered:
                raise ContractViolationError(
                    f"Input key '{key}' matches forbidden privileged token '{token}'."
                )
