"""Locked contracts, dimensions, and security boundaries for HOROS World Model vNext (HCTT-256).

Authoritative references:
- manifests/horos_world_model_vnext_manifest.json (schema horos_world_model_vnext_manifest_v1.1)
- experiments/world_model_vnext/horos_vnext_locked_training_spec.md (HOROS-P1-R002-SPEC-LOCKED-001)
- experiments/world_model_vnext/horos_world_model_vnext_spec.md
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Set, Tuple


@dataclass(frozen=True)
class TaskInterfaceSpec:
    """Immutable physical input/output interface for one robotic interaction task."""

    name: str
    task_index: int
    state_dim: int
    action_dim: int
    wrench_dim: int
    canonical_episode_length: int
    allowed_episode_lengths: Tuple[int, ...] = ()
    dt_s: float = 0.01


@dataclass(frozen=True)
class HCTTArchitectureConfig:
    """Frozen HCTT-256 architectural hyperparameters (HOROS-P1-R002-SPEC-LOCKED-001)."""

    model_family: str = "History-Conditioned Temporal Transformer (HCTT)"
    model_identifier: str = "HOROS-WM-vNext-alpha"
    objective_profile: str = "locked_4term_with_distillation"
    objective_authorized_for_training: bool = False
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

# Separate, unactivated teacher-free experimental objective proposed in Stage 3.5.
# Preserves locked 4-term spec intact; requires formal sign-off before Stage 4 activation.
EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG = HCTTArchitectureConfig(
    model_identifier="HOROS-WM-vNext-alpha-exp3term",
    objective_profile="experimental_teacher_free_3term_no_distill",
    objective_authorized_for_training=False,
    weight_sim_dynamics=1.0,
    weight_mode_focal=0.5,
    weight_sys_id=0.25,
    weight_distill=0.0,
)

TASK_INTERFACES: Dict[str, TaskInterfaceSpec] = {
    "compliant_prelift": TaskInterfaceSpec(
        name="compliant_prelift",
        task_index=0,
        state_dim=23,
        action_dim=5,
        wrench_dim=6,
        canonical_episode_length=20,
        allowed_episode_lengths=(20, 40),
        dt_s=0.01,
    ),
    "peg_in_hole": TaskInterfaceSpec(
        name="peg_in_hole",
        task_index=1,
        state_dim=15,
        action_dim=6,
        wrench_dim=6,
        canonical_episode_length=30,
        allowed_episode_lengths=(30,),
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


# ---------------------------------------------------------------------------
# Stage 3.6 Blind Holdout Quarantine & Evaluation Integrity Contracts
# ---------------------------------------------------------------------------

class CompromisedHoldoutError(ContractViolationError):
    """Raised when quarantined Stage 3.5 final holdout data or identifiers are accessed or consumed."""


class HoldoutIneligibleError(ContractViolationError):
    """Raised when an uncertified or ineligible holdout dataset is submitted for benchmark evaluation."""


COMPROMISED_HOLDOUT_ID: str = "stage35_final_holdout"
COMPROMISED_HOLDOUT_STATUS: str = "COMPROMISED_PUBLIC_EXPOSURE"
COMPROMISED_HOLDOUT_PROTOCOL: str = "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT"

COMPROMISED_HOLDOUT_SEED_RANGES: Dict[str, Tuple[int, int]] = {
    "compliant_prelift_nominal": (3531000, 3531029),
    "compliant_prelift_probe": (3532000, 3532017),
    "compliant_prelift_nonstationary": (3533000, 3533011),
    "peg_in_hole_nominal": (3541000, 3541029),
    "peg_in_hole_impact": (3544000, 3544017),
    "peg_in_hole_wedging": (3545000, 3545011),
}

COMPROMISED_HOLDOUT_PATH_TOKENS: FrozenSet[str] = frozenset({
    "final_holdout",
    "stage35_final_holdout",
    "stage35_production/final_holdout",
    "stage35_production\\final_holdout",
})

_COMPROMISED_SEEDS_SET: FrozenSet[int] = frozenset(
    s
    for start, end in COMPROMISED_HOLDOUT_SEED_RANGES.values()
    for s in range(start, end + 1)
)

_COMPROMISED_SAMPLE_IDS_CACHE: Optional[FrozenSet[str]] = None
_COMPROMISED_NPZ_SHA256_CACHE: Optional[FrozenSet[str]] = None
_COMPROMISED_PRIV_SHA256_CACHE: Optional[FrozenSet[str]] = None


def compute_file_sha256(path: Path | str, chunk_size: int = 65536) -> str:
    """Computes streaming SHA-256 digest of a file without loading entire file or deserializing."""
    p = Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _load_quarantine_record_data() -> Dict[str, Any]:
    base_dir = Path(__file__).resolve().parent.parent.parent
    qrec_path = base_dir / "experiments" / "stage36_holdout_quarantine" / "holdout_quarantine_record.json"
    if not qrec_path.is_file():
        raise ContractViolationError(f"Mandatory quarantine record missing: {qrec_path}")
    try:
        return json.loads(qrec_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ContractViolationError(f"Mandatory quarantine record malformed JSON: {qrec_path} ({exc})") from exc


def reset_quarantine_caches() -> None:
    """Resets the in-memory quarantine digest caches (for testing and verification)."""
    global _COMPROMISED_SAMPLE_IDS_CACHE, _COMPROMISED_NPZ_SHA256_CACHE, _COMPROMISED_PRIV_SHA256_CACHE
    _COMPROMISED_SAMPLE_IDS_CACHE = None
    _COMPROMISED_NPZ_SHA256_CACHE = None
    _COMPROMISED_PRIV_SHA256_CACHE = None


def get_compromised_sample_ids() -> FrozenSet[str]:
    """Returns the immutable set of 120 compromised Stage 3.5 final holdout sample IDs.
    Fails closed if the quarantine registry is missing or malformed.
    """
    global _COMPROMISED_SAMPLE_IDS_CACHE
    if _COMPROMISED_SAMPLE_IDS_CACHE is not None:
        return _COMPROMISED_SAMPLE_IDS_CACHE

    qrec = _load_quarantine_record_data()
    ids = set(qrec.get("affected_episodes", {}).get("sample_ids", []))
    if len(ids) != 120:
        raise ContractViolationError(
            f"Mandatory quarantine record has invalid sample_ids count ({len(ids)} != 120)"
        )
    _COMPROMISED_SAMPLE_IDS_CACHE = frozenset(ids)
    return _COMPROMISED_SAMPLE_IDS_CACHE


def get_compromised_npz_sha256() -> FrozenSet[str]:
    """Returns the immutable set of 120 compromised Stage 3.5 NPZ trajectory SHA-256 digests.
    Fails closed if the quarantine registry is missing or malformed.
    """
    global _COMPROMISED_NPZ_SHA256_CACHE
    if _COMPROMISED_NPZ_SHA256_CACHE is not None:
        return _COMPROMISED_NPZ_SHA256_CACHE

    qrec = _load_quarantine_record_data()
    shas = set(qrec.get("affected_episodes", {}).get("npz_sha256_digests", []))
    if len(shas) != 120:
        raise ContractViolationError(
            f"Mandatory quarantine record has invalid npz_sha256_digests count ({len(shas)} != 120)"
        )
    _COMPROMISED_NPZ_SHA256_CACHE = frozenset(shas)
    return _COMPROMISED_NPZ_SHA256_CACHE


def get_compromised_privileged_sha256() -> FrozenSet[str]:
    """Returns the immutable set of 120 compromised Stage 3.5 privileged JSON SHA-256 digests.
    Fails closed if the quarantine registry is missing or malformed.
    """
    global _COMPROMISED_PRIV_SHA256_CACHE
    if _COMPROMISED_PRIV_SHA256_CACHE is not None:
        return _COMPROMISED_PRIV_SHA256_CACHE

    qrec = _load_quarantine_record_data()
    shas = set(qrec.get("affected_episodes", {}).get("privileged_json_sha256_digests", []))
    if len(shas) != 120:
        raise ContractViolationError(
            f"Mandatory quarantine record has invalid privileged_json_sha256_digests count ({len(shas)} != 120)"
        )
    _COMPROMISED_PRIV_SHA256_CACHE = frozenset(shas)
    return _COMPROMISED_PRIV_SHA256_CACHE


def is_compromised_holdout_file_content(path: Path | str) -> bool:
    """Returns True if the file's raw content SHA-256 matches any quarantined archive or audit target."""
    p = Path(path)
    if not p.is_file():
        return False
    digest = compute_file_sha256(p)
    return (digest in get_compromised_npz_sha256()) or (digest in get_compromised_privileged_sha256())


def is_compromised_holdout_seed(seed: int) -> bool:
    """Returns True if the seed falls within the quarantined Stage 3.5 holdout seed pool."""
    return int(seed) in _COMPROMISED_SEEDS_SET


def is_compromised_holdout_sample_id(sample_id: str) -> bool:
    """Returns True if the sample_id belongs to the quarantined Stage 3.5 holdout dataset."""
    return str(sample_id) in get_compromised_sample_ids()


def is_compromised_holdout_path(path: Path | str) -> bool:
    """Returns True if the path contains quarantined holdout directory or file signatures."""
    p_str = str(path).replace("\\", "/").lower()
    for token in ("final_holdout", "stage35_final_holdout"):
        if token in p_str:
            return True
    fname = Path(path).stem.lower()
    if fname.startswith("syn_") and "final_holdout" in fname:
        return True
    if is_compromised_holdout_sample_id(fname):
        return True
    return False


def assert_not_compromised_holdout_data(item: Any, context: str = "") -> None:
    """Fail-closed assertion that an item (path, seed, sample_id, file content) is NOT from the quarantined holdout."""
    if isinstance(item, (str, Path)):
        p = Path(item)
        if is_compromised_holdout_path(item):
            raise CompromisedHoldoutError(
                f"Path '{item}' belongs to quarantined dataset '{COMPROMISED_HOLDOUT_ID}' "
                f"(status: {COMPROMISED_HOLDOUT_STATUS}). Access prohibited{f' [{context}]' if context else ''}."
            )
        if p.is_file() and is_compromised_holdout_file_content(p):
            raise CompromisedHoldoutError(
                f"File '{p.name}' content matches quarantined SHA-256 digest from dataset '{COMPROMISED_HOLDOUT_ID}' "
                f"(status: {COMPROMISED_HOLDOUT_STATUS}). Access prohibited{f' [{context}]' if context else ''}."
            )
        if isinstance(item, str) and is_compromised_holdout_sample_id(item):
            raise CompromisedHoldoutError(
                f"Sample ID '{item}' belongs to quarantined dataset '{COMPROMISED_HOLDOUT_ID}' "
                f"(status: {COMPROMISED_HOLDOUT_STATUS}). Access prohibited{f' [{context}]' if context else ''}."
            )
    elif isinstance(item, int):
        if is_compromised_holdout_seed(item):
            raise CompromisedHoldoutError(
                f"Seed '{item}' belongs to quarantined dataset '{COMPROMISED_HOLDOUT_ID}' "
                f"(status: {COMPROMISED_HOLDOUT_STATUS}). Access prohibited{f' [{context}]' if context else ''}."
            )


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
