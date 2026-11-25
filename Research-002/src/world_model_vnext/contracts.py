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


class QuarantineRegistryCorruptedError(ContractViolationError):
    """Raised when quarantine registry digest, schema, path, or content fails atomic integrity verification."""


COMPROMISED_HOLDOUT_ID: str = "stage35_final_holdout"
COMPROMISED_HOLDOUT_STATUS: str = "COMPROMISED_PUBLIC_EXPOSURE"
COMPROMISED_HOLDOUT_PROTOCOL: str = "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT"

# Cryptographically pinned SHA-256 fingerprint of the canonical quarantine registry.
# Calculated over exact canonical bytes of experiments/stage36_holdout_quarantine/holdout_quarantine_record.json.
# Trust assumption: Pinned in source code and guarded by repository version control.
EXPECTED_QUARANTINE_REGISTRY_SHA256: str = "f46319f0dcbf37ccb336807287ff7ea7cfae25f96150f0e747c32cd9d61a3f8c"
EXPECTED_QUARANTINE_EPISODE_COUNT: int = 120

CANONICAL_QUARANTINE_REGISTRY_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "experiments"
    / "stage36_holdout_quarantine"
    / "holdout_quarantine_record.json"
)

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
_CACHED_QUARANTINE_RECORD: Optional[Dict[str, Any]] = None


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


def validate_quarantine_registry_schema(data: Any) -> None:
    """Validates the complete quarantine registry schema, exact counts, formats, and non-duplication."""
    if not isinstance(data, dict):
        raise QuarantineRegistryCorruptedError("Quarantine registry root must be a JSON object")

    required_root_keys = {
        "quarantine_id",
        "status",
        "timestamp",
        "holdout_id",
        "protocol",
        "exposure_incident",
        "affected_episodes",
        "eligibility_flags",
        "historical_validation_state",
        "quarantine_enforcement",
        "remediation_requirements",
    }
    missing_root = required_root_keys - set(data.keys())
    if missing_root:
        raise QuarantineRegistryCorruptedError(
            f"Quarantine registry missing required root keys: {sorted(missing_root)}"
        )

    if data.get("status") != COMPROMISED_HOLDOUT_STATUS:
        raise QuarantineRegistryCorruptedError(
            f"Quarantine registry status mismatch: '{data.get('status')}' != '{COMPROMISED_HOLDOUT_STATUS}'"
        )
    if data.get("holdout_id") != COMPROMISED_HOLDOUT_ID:
        raise QuarantineRegistryCorruptedError(
            f"Quarantine registry holdout_id mismatch: '{data.get('holdout_id')}' != '{COMPROMISED_HOLDOUT_ID}'"
        )
    if data.get("protocol") != COMPROMISED_HOLDOUT_PROTOCOL:
        raise QuarantineRegistryCorruptedError(
            f"Quarantine registry protocol mismatch: '{data.get('protocol')}' != '{COMPROMISED_HOLDOUT_PROTOCOL}'"
        )

    affected = data.get("affected_episodes")
    if not isinstance(affected, dict):
        raise QuarantineRegistryCorruptedError("affected_episodes must be a JSON object")

    required_affected_keys = {
        "total_count",
        "compliant_prelift_count",
        "peg_in_hole_count",
        "horizon_distribution",
        "seed_ranges",
        "sample_ids",
        "canonical_episode_ids",
        "npz_sha256_digests",
        "privileged_json_sha256_digests",
    }
    missing_affected = required_affected_keys - set(affected.keys())
    if missing_affected:
        raise QuarantineRegistryCorruptedError(
            f"affected_episodes missing required keys: {sorted(missing_affected)}"
        )

    if affected.get("total_count") != EXPECTED_QUARANTINE_EPISODE_COUNT:
        raise QuarantineRegistryCorruptedError(
            f"affected_episodes.total_count mismatch: {affected.get('total_count')} != {EXPECTED_QUARANTINE_EPISODE_COUNT}"
        )
    if affected.get("compliant_prelift_count") != 60:
        raise QuarantineRegistryCorruptedError("compliant_prelift_count must be 60")
    if affected.get("peg_in_hole_count") != 60:
        raise QuarantineRegistryCorruptedError("peg_in_hole_count must be 60")

    def _validate_string_list(field: str, expected_len: int, check_hex: bool = False) -> None:
        val = affected.get(field)
        if not isinstance(val, list):
            raise QuarantineRegistryCorruptedError(f"{field} must be a JSON list")
        if len(val) != expected_len:
            raise QuarantineRegistryCorruptedError(
                f"{field} count mismatch: {len(val)} != {expected_len}"
            )
        if len(set(val)) != len(val):
            raise QuarantineRegistryCorruptedError(f"{field} contains duplicate entries")
        for item in val:
            if not isinstance(item, str) or not item.strip():
                raise QuarantineRegistryCorruptedError(f"{field} contains invalid non-string or empty item")
            if check_hex:
                if len(item) != 64 or not all(c in "0123456789abcdef" for c in item):
                    raise QuarantineRegistryCorruptedError(
                        f"{field} contains malformed SHA-256 digest: '{item}'"
                    )

    _validate_string_list("npz_sha256_digests", EXPECTED_QUARANTINE_EPISODE_COUNT, check_hex=True)
    _validate_string_list("privileged_json_sha256_digests", EXPECTED_QUARANTINE_EPISODE_COUNT, check_hex=True)
    _validate_string_list("sample_ids", EXPECTED_QUARANTINE_EPISODE_COUNT, check_hex=True)
    _validate_string_list("canonical_episode_ids", EXPECTED_QUARANTINE_EPISODE_COUNT, check_hex=False)

    ranges = affected.get("seed_ranges")
    if not isinstance(ranges, dict):
        raise QuarantineRegistryCorruptedError("seed_ranges must be a JSON object")
    for r_name, r_bounds in ranges.items():
        if not isinstance(r_bounds, (list, tuple)) or len(r_bounds) != 2:
            raise QuarantineRegistryCorruptedError(f"seed_range '{r_name}' must be a 2-element sequence")
        start, end = r_bounds
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            raise QuarantineRegistryCorruptedError(f"seed_range '{r_name}' has invalid bounds: {r_bounds}")


def read_and_verify_quarantine_registry(
    registry_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Atomic fail-closed reading, hashing, verification, and schema validation of quarantine registry.
    Reads file bytes exactly ONCE and parses those exact verified bytes.
    Prevents TOCTOU vulnerabilities and rejects path redirection.
    The expected SHA-256 fingerprint is strictly pinned to EXPECTED_QUARANTINE_REGISTRY_SHA256
    and cannot be overridden by callers, parameters, or environment variables.
    """
    canonical_path = CANONICAL_QUARANTINE_REGISTRY_PATH.resolve()
    if registry_path is not None:
        resolved_path = Path(registry_path).resolve()
        if resolved_path != canonical_path:
            raise QuarantineRegistryCorruptedError(
                f"Quarantine registry path redirection rejected: '{registry_path}' != canonical '{canonical_path}'"
            )
        target_path = resolved_path
    else:
        target_path = canonical_path

    if not target_path.is_file():
        raise QuarantineRegistryCorruptedError(f"Mandatory quarantine record missing: {target_path}")

    # 1. Read bytes exactly once
    try:
        raw_bytes = target_path.read_bytes()
    except Exception as exc:
        raise QuarantineRegistryCorruptedError(
            f"Unreadable quarantine registry at '{target_path}': {exc}"
        ) from exc

    # 2. Hash exact bytes and verify against pinned constant BEFORE json parsing
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != EXPECTED_QUARANTINE_REGISTRY_SHA256:
        raise QuarantineRegistryCorruptedError(
            f"Quarantine registry SHA-256 mismatch: calculated {actual_sha256} != expected {EXPECTED_QUARANTINE_REGISTRY_SHA256}. "
            "Registry integrity compromised; parsing rejected."
        )

    # 3. Parse the verified byte sequence (atomic, TOCTOU-free)
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise QuarantineRegistryCorruptedError(
            f"Quarantine registry contains invalid JSON: {exc}"
        ) from exc

    # 4. Strict schema validation
    validate_quarantine_registry_schema(parsed)

    return parsed


def _load_quarantine_record_data(registry_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Internal loader enforcing atomic verification with in-memory caching."""
    global _CACHED_QUARANTINE_RECORD
    if registry_path is None and _CACHED_QUARANTINE_RECORD is not None:
        return _CACHED_QUARANTINE_RECORD
    record = read_and_verify_quarantine_registry(registry_path=registry_path)
    if registry_path is None:
        _CACHED_QUARANTINE_RECORD = record
    return record


def load_quarantine_record(registry_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Public entrypoint to load and verify the quarantine registry record."""
    return _load_quarantine_record_data(registry_path=registry_path)


def verify_quarantine_registry_integrity() -> str:
    """Verifies quarantine registry integrity and returns the verified SHA-256 fingerprint."""
    _load_quarantine_record_data()
    return EXPECTED_QUARANTINE_REGISTRY_SHA256


def reset_quarantine_caches() -> None:
    """Resets the in-memory quarantine digest caches (for testing and verification)."""
    global _COMPROMISED_SAMPLE_IDS_CACHE, _COMPROMISED_NPZ_SHA256_CACHE, _COMPROMISED_PRIV_SHA256_CACHE, _CACHED_QUARANTINE_RECORD
    _COMPROMISED_SAMPLE_IDS_CACHE = None
    _COMPROMISED_NPZ_SHA256_CACHE = None
    _COMPROMISED_PRIV_SHA256_CACHE = None
    _CACHED_QUARANTINE_RECORD = None


def get_compromised_sample_ids() -> FrozenSet[str]:
    """Returns the immutable set of 120 compromised Stage 3.5 final holdout sample IDs.
    Fails closed if the quarantine registry is missing or malformed.
    """
    global _COMPROMISED_SAMPLE_IDS_CACHE
    if _COMPROMISED_SAMPLE_IDS_CACHE is not None:
        return _COMPROMISED_SAMPLE_IDS_CACHE

    qrec = _load_quarantine_record_data()
    ids = set(qrec.get("affected_episodes", {}).get("sample_ids", []))
    if len(ids) != EXPECTED_QUARANTINE_EPISODE_COUNT:
        raise ContractViolationError(
            f"Mandatory quarantine record has invalid sample_ids count ({len(ids)} != {EXPECTED_QUARANTINE_EPISODE_COUNT})"
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
    if len(shas) != EXPECTED_QUARANTINE_EPISODE_COUNT:
        raise ContractViolationError(
            f"Mandatory quarantine record has invalid npz_sha256_digests count ({len(shas)} != {EXPECTED_QUARANTINE_EPISODE_COUNT})"
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
    if len(shas) != EXPECTED_QUARANTINE_EPISODE_COUNT:
        raise ContractViolationError(
            f"Mandatory quarantine record has invalid privileged_json_sha256_digests count ({len(shas)} != {EXPECTED_QUARANTINE_EPISODE_COUNT})"
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


# ---------------------------------------------------------------------------
# Stage 3.7 Independent Blind Evaluation Protocol Contracts
# ---------------------------------------------------------------------------

class ManifestSignatureError(ContractViolationError):
    """Raised when an evaluation manifest digital signature fails cryptographic verification."""


class EvaluationAlreadyExecutedError(ContractViolationError):
    """Raised when an evaluation is attempted against a previously consumed blind holdout."""


def strip_forbidden_model_input_keys(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Strips all privileged, target, seed, and metadata keys, returning a sanitized copy.
    
    Verifies that the returned dictionary satisfies assert_no_privileged_keys_in_inputs.
    """
    sanitized: Dict[str, Any] = {}
    for k, v in inputs.items():
        lowered = str(k).lower()
        if lowered in FORBIDDEN_MODEL_INPUT_KEYS:
            continue
        is_forbidden_token = False
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
                is_forbidden_token = True
                break
        if not is_forbidden_token:
            sanitized[k] = v
            
    assert_no_privileged_keys_in_inputs(sanitized)
    return sanitized


def compute_manifest_signature(manifest_bytes: bytes, secret_key: bytes) -> str:
    """Generates deterministic HMAC-SHA256 signature string for a manifest byte sequence."""
    import hmac
    return hmac.new(secret_key, manifest_bytes, hashlib.sha256).hexdigest()


def verify_signed_manifest_signature(
    manifest_bytes: bytes,
    signature_hex: str,
    verification_key: bytes,
) -> bool:
    """Cryptographically verifies manifest signature against verification key in constant time."""
    import hmac
    try:
        expected_sig = compute_manifest_signature(manifest_bytes, verification_key)
        return hmac.compare_digest(expected_sig.lower(), signature_hex.lower())
    except Exception as exc:
        raise ManifestSignatureError(f"Signature verification failed due to cryptographic error: {exc}") from exc


def verify_holdout_archive_digests(
    manifest_data: Dict[str, Any],
    archives_by_name: Dict[str, bytes],
) -> None:
    """Verifies that all archives in manifest match exact streaming SHA-256 digests."""
    if not isinstance(manifest_data, dict) or "archives" not in manifest_data:
        raise ContractViolationError("Manifest missing 'archives' digest table.")
    
    expected_archives = manifest_data["archives"]
    if not isinstance(expected_archives, dict):
        raise ContractViolationError("'archives' entry in manifest must be a dictionary.")

    if len(expected_archives) != len(archives_by_name):
        raise ContractViolationError(
            f"Archive count mismatch: manifest expects {len(expected_archives)}, received {len(archives_by_name)}."
        )

    for name, expected_sha in expected_archives.items():
        if name not in archives_by_name:
            raise ContractViolationError(f"Expected archive '{name}' missing from provided archives.")
        actual_sha = hashlib.sha256(archives_by_name[name]).hexdigest().lower()
        if actual_sha != expected_sha.lower():
            raise ContractViolationError(
                f"Archive digest mismatch for '{name}': expected {expected_sha}, got {actual_sha}."
            )


def enforce_one_time_evaluation_lock(
    consumed_ledger: Set[Tuple[str, str]],
    checkpoint_sha: str,
    manifest_sha: str,
) -> None:
    """Enforces the one-time evaluation rule, blocking repeated evaluation on the same holdout."""
    pair = (str(checkpoint_sha).strip().lower(), str(manifest_sha).strip().lower())
    if pair in consumed_ledger:
        raise EvaluationAlreadyExecutedError(
            f"Evaluation pair (checkpoint: {checkpoint_sha[:8]}..., manifest: {manifest_sha[:8]}...) "
            "has already been evaluated. Repeated querying of a blind holdout is strictly prohibited."
        )


def verify_redacted_benchmark_output(output_report: Dict[str, Any]) -> None:
    """Validates that a benchmark summary report contains only aggregate data and zero privileged leakage."""
    if not isinstance(output_report, dict):
        raise ContractViolationError("Benchmark output must be a dictionary.")

    forbidden_output_tokens = (
        "step_mu_true",
        "step_mass_kg",
        "raw_trajectories",
        "per_episode_targets",
        "seeds",
        "episode_seeds",
        "private_identifiers",
    )
    for k in output_report.keys():
        for token in forbidden_output_tokens:
            if token in str(k).lower():
                raise ContractViolationError(
                    f"Redacted benchmark output contains forbidden detailed ground truth token '{k}'."
                )

    if "per_episode_errors" in output_report:
        raise ContractViolationError("Per-episode errors must not be exposed in public benchmark output.")

