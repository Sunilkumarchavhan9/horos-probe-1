"""HOROS PROBE-1 — Research-002: Stage 3.6D Quarantine Registry Integrity Anchoring Tests.

Covers all 20 mandatory verification requirements across Registry Fingerprint,
Atomic Verification, and Governance:

Registry Fingerprint:
1. The authentic canonical registry passes.
2. A single-byte registry modification fails.
3. A different but structurally valid registry fails.
4. A registry with the correct entry count but substituted digest values fails.
5. Registry path redirection is rejected.
6. Missing registry fails closed.
7. Unreadable registry fails closed.
8. Invalid JSON fails closed.
9. Duplicate entries fail closed.
10. Missing entries fail closed.
11. Malformed digest values fail closed.
12. Unexpected schema entries are handled according to an explicit strict schema.

Atomic Verification:
13. The bytes hashed are exactly the bytes parsed.
14. No second registry read can replace the verified data between checking and parsing.
15. A hash mismatch prevents JSON parsing.
16. A registry-verification failure blocks protected archive ingestion.

Governance:
17. Training remains locked (TRAINING_PERMITTED=0, EXECUTION_AUTHORIZED=0).
18. The old holdout remains permanently ineligible.
19. Final evaluation remains blocked.
20. No replacement holdout is generated.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

TESTS_DIR = Path(__file__).resolve().parent
R002_ROOT = TESTS_DIR.parent
for p in (R002_ROOT, R002_ROOT / "src"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)

from scripts.stage3_production_adapter import (  # noqa: E402
    ProductionTrainingDataloader,
    SecurityViolationError,
)
from world_model_vnext import (  # noqa: E402
    AuditTargetLoader,
    CANONICAL_QUARANTINE_REGISTRY_PATH,
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    EXECUTION_AUTHORIZED,
    EXPECTED_QUARANTINE_EPISODE_COUNT,
    EXPECTED_QUARANTINE_REGISTRY_SHA256,
    HCTTReadOnlyEvaluator,
    HCTTTemporalDataset,
    HCTTTrainingRunner,
    HoldoutIneligibleError,
    QuarantineRegistryCorruptedError,
    TRAINING_PERMITTED,
    assert_split_eligible_for_evaluation,
    get_compromised_npz_sha256,
    get_compromised_privileged_sha256,
    get_compromised_sample_ids,
    is_compromised_holdout_file_content,
    load_quarantine_record,
    read_and_verify_quarantine_registry,
    reset_quarantine_caches,
    validate_quarantine_registry_schema,
    verify_quarantine_registry_integrity,
)

S35_HOLDOUT = R002_ROOT / "experiments" / "stage35_production" / "final_holdout"
S35_HOLDOUT_TRAIN = S35_HOLDOUT / "training"


@pytest.fixture(autouse=True)
def clean_quarantine_caches():
    """Ensure in-memory caches are pristine before and after each test."""
    reset_quarantine_caches()
    yield
    reset_quarantine_caches()


@pytest.fixture
def canonical_bytes() -> bytes:
    """Reads raw bytes of the canonical quarantine registry."""
    return CANONICAL_QUARANTINE_REGISTRY_PATH.read_bytes()


@pytest.fixture
def canonical_data(canonical_bytes: bytes) -> Dict[str, Any]:
    """Parses authentic quarantine data."""
    return json.loads(canonical_bytes.decode("utf-8"))


@pytest.fixture
def dummy_archive(tmp_path: Path) -> Path:
    """Creates a temporary mock non-compromised .npz archive."""
    npz_path = tmp_path / "mock_archive.npz"
    np.savez(
        npz_path,
        sample_id=np.array("mock_test_id_stage36d"),
        task=np.array("compliant_prelift"),
        states=np.zeros((20, 24), dtype=np.float32),
        actions=np.zeros((20, 4), dtype=np.float32),
        next_states=np.zeros((20, 24), dtype=np.float32),
        wrenches=np.zeros((20, 6), dtype=np.float32),
        next_wrenches=np.zeros((20, 6), dtype=np.float32),
    )
    return npz_path


# ===========================================================================
# 1-12: Registry Fingerprint & Integrity Tests
# ===========================================================================

def test_01_authentic_canonical_registry_passes(canonical_bytes: bytes):
    """The authentic canonical registry passes hash check and returns exactly 120 items."""
    computed_sha = hashlib.sha256(canonical_bytes).hexdigest()
    assert computed_sha == EXPECTED_QUARANTINE_REGISTRY_SHA256, (
        f"Authentic registry bytes must match EXPECTED_QUARANTINE_REGISTRY_SHA256"
    )

    verified_sha = verify_quarantine_registry_integrity()
    assert verified_sha == EXPECTED_QUARANTINE_REGISTRY_SHA256

    data = load_quarantine_record()
    assert data["affected_episodes"]["total_count"] == EXPECTED_QUARANTINE_EPISODE_COUNT
    assert len(data["affected_episodes"]["npz_sha256_digests"]) == 120
    assert len(data["affected_episodes"]["privileged_json_sha256_digests"]) == 120
    assert len(data["affected_episodes"]["sample_ids"]) == 120


def test_02_single_byte_registry_modification_fails(canonical_bytes: bytes):
    """A single-byte modification anywhere in the registry file causes verification failure."""
    # Modify a single byte in the buffer
    corrupted_bytes = bytearray(canonical_bytes)
    # Flip one character in the middle
    corrupted_bytes[100] = ord(b"X") if corrupted_bytes[100] != ord(b"X") else ord(b"Y")
    corrupted_bytes = bytes(corrupted_bytes)

    with patch.object(Path, "read_bytes", return_value=corrupted_bytes):
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "SHA-256 mismatch" in str(exc_info.value)


def test_03_structurally_valid_different_registry_fails():
    """A different but structurally valid JSON registry fails due to cryptographic mismatch."""
    substitute_data = {
        "quarantine_id": "SUBSTITUTE-REGISTRY",
        "status": COMPROMISED_HOLDOUT_STATUS,
        "timestamp": "2026-11-25T00:00:00Z",
        "holdout_id": COMPROMISED_HOLDOUT_ID,
        "protocol": "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT",
        "exposure_incident": {},
        "affected_episodes": {"total_count": 0},
        "eligibility_flags": {},
        "historical_validation_state": {},
        "quarantine_enforcement": {},
        "remediation_requirements": [],
    }
    substitute_bytes = json.dumps(substitute_data, indent=2).encode("utf-8")

    with patch.object(Path, "read_bytes", return_value=substitute_bytes):
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "SHA-256 mismatch" in str(exc_info.value)


def test_04_registry_with_correct_count_but_substituted_digests_fails(canonical_data: Dict[str, Any]):
    """A registry with exactly 120 valid digests but substituted values fails hash verification."""
    tampered_data = copy.deepcopy(canonical_data)
    # Replace one digest with another valid 64-char hex string
    tampered_data["affected_episodes"]["npz_sha256_digests"][0] = "a" * 64
    tampered_bytes = json.dumps(tampered_data, indent=2).encode("utf-8")

    with patch.object(Path, "read_bytes", return_value=tampered_bytes):
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "SHA-256 mismatch" in str(exc_info.value)


def test_05_registry_path_redirection_is_rejected(tmp_path: Path, canonical_bytes: bytes):
    """Attempting to point the registry to a path outside the canonical relative path is rejected."""
    redirect_file = tmp_path / "rogue_quarantine_record.json"
    redirect_file.write_bytes(canonical_bytes)

    with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
        read_and_verify_quarantine_registry(registry_path=redirect_file)
    assert "redirection rejected" in str(exc_info.value).lower()


def test_06_missing_registry_fails_closed():
    """Missing registry file raises QuarantineRegistryCorruptedError fail-closed."""
    with patch.object(Path, "is_file", return_value=False):
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "missing" in str(exc_info.value).lower()


def test_07_unreadable_registry_fails_closed():
    """Unreadable registry due to I/O or permissions raises QuarantineRegistryCorruptedError."""
    with patch.object(Path, "read_bytes", side_effect=PermissionError("Access denied")):
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "unreadable" in str(exc_info.value).lower()


def test_08_invalid_json_fails_closed():
    """If bytes pass hash check but are invalid JSON, loading fails closed."""
    with patch("world_model_vnext.contracts.json.loads", side_effect=json.JSONDecodeError("Expecting value", "...", 0)):
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "invalid json" in str(exc_info.value).lower()


def test_09_duplicate_entries_fail_closed(canonical_data: Dict[str, Any]):
    """Duplicate entries in digest lists fail schema validation."""
    tampered_data = copy.deepcopy(canonical_data)
    # Duplicate first element to replace second
    tampered_data["affected_episodes"]["npz_sha256_digests"][1] = (
        tampered_data["affected_episodes"]["npz_sha256_digests"][0]
    )

    with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
        validate_quarantine_registry_schema(tampered_data)
    assert "duplicate entries" in str(exc_info.value).lower()


def test_10_missing_entries_fail_closed(canonical_data: Dict[str, Any]):
    """Missing entries (< 120) fail schema validation."""
    tampered_data = copy.deepcopy(canonical_data)
    # Pop one digest
    tampered_data["affected_episodes"]["npz_sha256_digests"].pop()

    with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
        validate_quarantine_registry_schema(tampered_data)
    assert "count mismatch" in str(exc_info.value).lower()


def test_11_malformed_digest_values_fail_closed(canonical_data: Dict[str, Any]):
    """Non-hex or wrong-length digest strings fail schema validation."""
    tampered_data = copy.deepcopy(canonical_data)
    # Set a 63-char hex string
    tampered_data["affected_episodes"]["npz_sha256_digests"][0] = "f" * 63

    with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
        validate_quarantine_registry_schema(tampered_data)
    assert "malformed sha-256" in str(exc_info.value).lower()


def test_12_unexpected_schema_entries_handled_strictly(canonical_data: Dict[str, Any]):
    """Missing required schema keys or incorrect status fails validation."""
    tampered_data = copy.deepcopy(canonical_data)
    del tampered_data["status"]

    with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
        validate_quarantine_registry_schema(tampered_data)
    assert "missing required root keys" in str(exc_info.value).lower()

    # Tampered status
    tampered_data2 = copy.deepcopy(canonical_data)
    tampered_data2["status"] = "UNCOMPROMISED"
    with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
        validate_quarantine_registry_schema(tampered_data2)
    assert "status mismatch" in str(exc_info.value).lower()


# ===========================================================================
# 13-16: Atomic Verification & Loader Protection Tests
# ===========================================================================

def test_13_bytes_hashed_are_exactly_bytes_parsed(canonical_bytes: bytes):
    """The raw byte buffer hashed is identical to the byte buffer parsed into JSON (TOCTOU-free)."""
    parsed_arg = None
    real_json_loads = json.loads

    def spy_loads(s: str) -> Dict[str, Any]:
        nonlocal parsed_arg
        parsed_arg = s
        return real_json_loads(s)

    with patch("world_model_vnext.contracts.json.loads", side_effect=spy_loads):
        data = read_and_verify_quarantine_registry()

    assert parsed_arg is not None
    # Bytes decoded must match canonical bytes exactly
    assert parsed_arg.encode("utf-8") == canonical_bytes
    assert data["affected_episodes"]["total_count"] == 120


def test_14_no_second_registry_read_between_checking_and_parsing():
    """Path.read_bytes() is invoked exactly once during atomic verification."""
    with patch.object(Path, "read_bytes", wraps=CANONICAL_QUARANTINE_REGISTRY_PATH.read_bytes) as mock_read:
        read_and_verify_quarantine_registry()
        assert mock_read.call_count == 1, (
            f"Expected exactly 1 read_bytes call, found {mock_read.call_count}"
        )


def test_15_hash_mismatch_prevents_json_parsing(canonical_bytes: bytes):
    """If hash verification fails, json.loads is never called."""
    corrupted_bytes = canonical_bytes + b"tampered"

    with patch.object(Path, "read_bytes", return_value=corrupted_bytes):
        with patch("world_model_vnext.contracts.json.loads") as mock_json_loads:
            with pytest.raises(QuarantineRegistryCorruptedError):
                read_and_verify_quarantine_registry()

            mock_json_loads.assert_not_called()


def test_16_registry_verification_failure_blocks_protected_archive_ingestion(
    dummy_archive: Path,
):
    """When registry integrity fails, dataloader archive ingestion fails closed before np.load."""
    loader = ProductionTrainingDataloader.__new__(ProductionTrainingDataloader)

    with patch(
        "world_model_vnext.contracts.read_and_verify_quarantine_registry",
        side_effect=QuarantineRegistryCorruptedError("Registry corrupted by attacker"),
    ):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError) as exc_info:
                loader._load_archive(dummy_archive)

            mock_np_load.assert_not_called()
            assert "failed closed" in str(exc_info.value).lower()


# ===========================================================================
# 17-20: Governance & Lock Verification Tests
# ===========================================================================

def test_17_training_remains_locked():
    """Training authorization constants and runner invariants remain strictly locked at 0."""
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    runner = HCTTTrainingRunner()
    assert runner.training_steps_executed == 0
    assert runner.parameter_updates_executed == 0
    runner.assert_training_locked()


def test_18_old_holdout_remains_permanently_ineligible():
    """The compromised Stage 3.5 final holdout is permanently ineligible for benchmark evaluation."""
    target_loader = AuditTargetLoader(R002_ROOT / "experiments" / "stage3_production")
    evaluator = HCTTReadOnlyEvaluator(target_loader=target_loader)

    for ineligible_name in (
        "stage35_final_holdout",
        "final_holdout",
        "final_test",
        "sealed_holdout",
    ):
        with pytest.raises(HoldoutIneligibleError) as exc_info:
            evaluator.verify_holdout_eligibility(ineligible_name)
        assert (
            "quarantined" in str(exc_info.value).lower()
            or "not_created" in str(exc_info.value).lower()
            or "blocked" in str(exc_info.value).lower()
        )


def test_19_final_evaluation_remains_blocked():
    """AuditTargetLoader and HCTTTemporalDataset strictly refuse initialization on compromised holdout."""
    with pytest.raises((CompromisedHoldoutError, HoldoutIneligibleError)) as exc_info:
        AuditTargetLoader(S35_HOLDOUT)
    assert "quarantined" in str(exc_info.value).lower()

    with pytest.raises(CompromisedHoldoutError) as exc_info2:
        HCTTTemporalDataset(S35_HOLDOUT_TRAIN)
    assert "quarantined" in str(exc_info2.value).lower()


def test_20_no_replacement_holdout_is_generated():
    """Verifies that no replacement holdout directories or files were generated."""
    # Check that no unauthorized holdout directories exist in stage35_production or experiments
    exp_dir = R002_ROOT / "experiments"
    unauthorized_dirs = [
        d.name
        for d in exp_dir.glob("*holdout*")
        if d.name not in {"stage36_holdout_quarantine"}
    ]
    assert unauthorized_dirs == [], f"Found unexpected holdout directories: {unauthorized_dirs}"


def test_21_caller_cannot_supply_replacement_expected_hash():
    """read_and_verify_quarantine_registry rejects any caller attempt to supply expected_sha256."""
    with pytest.raises(TypeError) as exc_info:
        read_and_verify_quarantine_registry(expected_sha256="a" * 64)  # type: ignore
    assert "unexpected keyword argument" in str(exc_info.value)


def test_22_attempted_hash_override_cannot_authorize_substituted_registry():
    """An adversary providing a substituted registry and its valid SHA-256 cannot bypass verification."""
    substitute_data = {
        "quarantine_id": "ROGUE-REGISTRY-ATTACK",
        "status": COMPROMISED_HOLDOUT_STATUS,
        "holdout_id": COMPROMISED_HOLDOUT_ID,
        "protocol": "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT",
        "affected_episodes": {"total_count": 0},
    }
    substitute_bytes = json.dumps(substitute_data, indent=2).encode("utf-8")
    substitute_sha = hashlib.sha256(substitute_bytes).hexdigest()

    # Even if attacker passes substitute_sha through kwargs or tries calling with modified bytes:
    with patch.object(Path, "read_bytes", return_value=substitute_bytes):
        # 1. Calling standard verifier fails with SHA mismatch against pinned constant
        with pytest.raises(QuarantineRegistryCorruptedError) as exc_info:
            read_and_verify_quarantine_registry()
        assert "SHA-256 mismatch" in str(exc_info.value)
        assert EXPECTED_QUARANTINE_REGISTRY_SHA256 in str(exc_info.value)

        # 2. Attempting to supply expected_sha256 fails with TypeError
        with pytest.raises(TypeError):
            read_and_verify_quarantine_registry(expected_sha256=substitute_sha)  # type: ignore

        # 3. High-level loaders fail closed
        with pytest.raises(QuarantineRegistryCorruptedError):
            load_quarantine_record()
