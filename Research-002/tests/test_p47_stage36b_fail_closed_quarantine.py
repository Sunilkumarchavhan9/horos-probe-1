"""HOROS PROBE-1 — Research-002: Stage 3.6B Fail-Closed Security & Final Acceptance Tests.

Covers all 10 mandatory Stage 3.6B verification requirements:
1. Quarantine verifier import failure blocks loading (SecurityViolationError).
2. Missing digest registry blocks loading (SecurityViolationError).
3. Malformed digest registry blocks loading (SecurityViolationError).
4. Verification function failure blocks loading (SecurityViolationError).
5. Exact compromised archive copied under neutral filename is rejected.
6. Exact compromised archive relocated to another directory is rejected.
7. np.load() is not called in any rejected case (mock_np_load.assert_not_called()).
8. A valid, authorized non-compromised training archive still loads normally.
9. Training authorization remains locked (TRAINING_PERMITTED=0, EXECUTION_AUTHORIZED=0).
10. The original 120-episode holdout remains ineligible for every use.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch

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
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_PROTOCOL,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    EXECUTION_AUTHORIZED,
    HCTTReadOnlyEvaluator,
    HCTTTrainingRunner,
    HoldoutIneligibleError,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
    get_compromised_npz_sha256,
    is_compromised_holdout_file_content,
    reset_quarantine_caches,
)

S33_PROD_TRAIN = R002_ROOT / "experiments" / "stage3_production" / "training"
S35_HOLDOUT = R002_ROOT / "experiments" / "stage35_production" / "final_holdout"
S35_HOLDOUT_TRAIN = S35_HOLDOUT / "training"
QREC_PATH = R002_ROOT / "experiments" / "stage36_holdout_quarantine" / "holdout_quarantine_record.json"


@pytest.fixture(autouse=True)
def clean_quarantine_caches():
    """Ensure quarantine caches are pristine before and after each test."""
    reset_quarantine_caches()
    yield
    reset_quarantine_caches()


@pytest.fixture
def dummy_archive(tmp_path: Path) -> Path:
    """Creates a temporary mock non-compromised .npz archive."""
    npz_path = tmp_path / "mock_archive.npz"
    np.savez(
        npz_path,
        sample_id=np.array("mock_test_id_001"),
        task=np.array("compliant_prelift"),
        states=np.zeros((20, 24), dtype=np.float32),
        actions=np.zeros((20, 4), dtype=np.float32),
        next_states=np.zeros((20, 24), dtype=np.float32),
        wrenches=np.zeros((20, 6), dtype=np.float32),
        next_wrenches=np.zeros((20, 6), dtype=np.float32),
    )
    return npz_path


@pytest.fixture
def quarantined_archive_bytes() -> bytes:
    """Reads raw bytes of one quarantined holdout archive without deserialization."""
    q_files = sorted(S35_HOLDOUT_TRAIN.glob("*.npz"))
    assert len(q_files) == 120, f"Expected 120 quarantined archives, found {len(q_files)}"
    return q_files[0].read_bytes()


def _get_loader_instance() -> ProductionTrainingDataloader:
    """Helper to instantiate dataloader bypasses directory scans for isolated method tests."""
    return ProductionTrainingDataloader.__new__(ProductionTrainingDataloader)


# ---------------------------------------------------------------------------
# 1. Quarantine verifier import failure blocks loading
# ---------------------------------------------------------------------------

def test_quarantine_verifier_import_failure_blocks_loading(dummy_archive: Path):
    """If world_model_vnext fails to import, _load_archive must raise SecurityViolationError and never call np.load."""
    loader = _get_loader_instance()

    with patch.dict("sys.modules", {"world_model_vnext": None}):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError) as exc_info:
                loader._load_archive(dummy_archive)

            mock_np_load.assert_not_called()
            assert "import failure" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 2. Missing digest registry blocks loading
# ---------------------------------------------------------------------------

def test_missing_digest_registry_blocks_loading(dummy_archive: Path):
    """If the quarantine registry file is missing, loading must fail closed with SecurityViolationError."""
    loader = _get_loader_instance()

    with patch("world_model_vnext.contracts._load_quarantine_record_data", side_effect=ContractViolationError("Registry missing")):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError) as exc_info:
                loader._load_archive(dummy_archive)

            mock_np_load.assert_not_called()
            assert "failed closed" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 3. Malformed digest registry blocks loading
# ---------------------------------------------------------------------------

def test_malformed_digest_registry_blocks_loading(dummy_archive: Path):
    """If the quarantine registry is malformed or has invalid digest counts, loading must fail closed."""
    loader = _get_loader_instance()

    # Case A: JSON syntax error / ContractViolationError
    with patch("world_model_vnext.contracts._load_quarantine_record_data", side_effect=ContractViolationError("Corrupt JSON")):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError) as exc_info:
                loader._load_archive(dummy_archive)

            mock_np_load.assert_not_called()
            assert "failed closed" in str(exc_info.value).lower()

    # Case B: Incomplete digests (< 120)
    malformed_data = {
        "affected_episodes": {
            "npz_sha256_digests": ["only_one_sha256_hash"],
            "sample_ids": [],
        }
    }
    with patch("world_model_vnext.contracts._load_quarantine_record_data", return_value=malformed_data):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError) as exc_info:
                loader._load_archive(dummy_archive)

            mock_np_load.assert_not_called()
            assert "failed closed" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 4. Verification function failure blocks loading
# ---------------------------------------------------------------------------

def test_verification_function_failure_blocks_loading(dummy_archive: Path):
    """If any internal verification function raises an unexpected exception, it must fail closed."""
    loader = _get_loader_instance()

    with patch("world_model_vnext.is_compromised_holdout_file_content", side_effect=RuntimeError("Disk read error")):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError) as exc_info:
                loader._load_archive(dummy_archive)

            mock_np_load.assert_not_called()
            assert "failed closed" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 5. Exact compromised archive copied under neutral filename is rejected
# ---------------------------------------------------------------------------

def test_exact_compromised_archive_copied_under_neutral_filename_rejected(
    tmp_path: Path, quarantined_archive_bytes: bytes
):
    """Compromised archive renamed to neutral filename is detected and rejected before np.load."""
    neutral_path = tmp_path / "innocent_sample_training_episode_9999.npz"
    neutral_path.write_bytes(quarantined_archive_bytes)

    loader = _get_loader_instance()

    with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
        with pytest.raises(SecurityViolationError) as exc_info:
            loader._load_archive(neutral_path)

        mock_np_load.assert_not_called()
        assert "digest rejected" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 6. Exact compromised archive relocated to another directory is rejected
# ---------------------------------------------------------------------------

def test_exact_compromised_archive_relocated_to_another_directory_rejected(
    tmp_path: Path, quarantined_archive_bytes: bytes
):
    """Compromised archive relocated into arbitrary directory tree is rejected before np.load."""
    nested_dir = tmp_path / "production_dataset_v2" / "clean_train"
    nested_dir.mkdir(parents=True)
    relocated_path = nested_dir / "clean_prelift_001.npz"
    relocated_path.write_bytes(quarantined_archive_bytes)

    loader = _get_loader_instance()

    with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
        with pytest.raises(SecurityViolationError) as exc_info:
            loader._load_archive(relocated_path)

        mock_np_load.assert_not_called()
        assert "digest rejected" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 7. np.load() is not called in any rejected case
# ---------------------------------------------------------------------------

def test_np_load_never_called_in_any_rejected_case(
    tmp_path: Path, dummy_archive: Path, quarantined_archive_bytes: bytes
):
    """Comprehensive check: across all rejection modalities, np.load is never invoked."""
    loader = _get_loader_instance()

    # Path-based rejection
    fake_holdout_path = tmp_path / "final_holdout" / "sample_001.npz"
    fake_holdout_path.parent.mkdir(parents=True)
    fake_holdout_path.write_bytes(b"dummy")

    with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
        with pytest.raises(SecurityViolationError):
            loader._load_archive(fake_holdout_path)
        mock_np_load.assert_not_called()

    # Content-digest rejection
    relocated = tmp_path / "disguised.npz"
    relocated.write_bytes(quarantined_archive_bytes)
    with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
        with pytest.raises(SecurityViolationError):
            loader._load_archive(relocated)
        mock_np_load.assert_not_called()

    # Import failure
    with patch.dict("sys.modules", {"world_model_vnext": None}):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError):
                loader._load_archive(dummy_archive)
            mock_np_load.assert_not_called()

    # Registry failure
    with patch("world_model_vnext.contracts._load_quarantine_record_data", side_effect=ContractViolationError("Fail")):
        with patch("scripts.stage3_production_adapter.np.load") as mock_np_load:
            with pytest.raises(SecurityViolationError):
                loader._load_archive(dummy_archive)
            mock_np_load.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Valid, authorized non-compromised training archive still loads normally
# ---------------------------------------------------------------------------

def test_valid_authorized_non_compromised_training_archive_loads_normally():
    """Authorized clean Stage 3.3 archive passes all quarantine checks and loads cleanly."""
    loader = _get_loader_instance()
    clean_files = sorted(S33_PROD_TRAIN.glob("*.npz"))
    assert len(clean_files) == 1000, f"Expected 1000 clean archives, found {len(clean_files)}"

    clean_sample = clean_files[0]
    package = loader._load_archive(clean_sample)

    assert isinstance(package, dict)
    assert "sample_id" in package
    assert "task" in package
    assert "states" in package
    assert "actions" in package
    assert "next_states" in package
    assert "wrenches" in package
    assert "next_wrenches" in package
    assert package["states"].ndim == 2
    assert package["next_states"].ndim == 2


# ---------------------------------------------------------------------------
# 9. Training authorization remains locked
# ---------------------------------------------------------------------------

def test_training_authorization_remains_locked():
    """Verify that training authorization invariants remain strictly enforced at zero updates."""
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    runner = HCTTTrainingRunner()
    assert runner.training_permitted == 0
    assert runner.execution_authorized == 0
    assert runner.training_steps_executed == 0
    assert runner.parameter_updates_executed == 0

    runner.assert_training_locked()

    with pytest.raises(TrainingAuthorizationError):
        runner.train(model=None, dataset=None)


# ---------------------------------------------------------------------------
# 10. The original 120-episode holdout remains ineligible for every use
# ---------------------------------------------------------------------------

def test_original_120_episode_holdout_remains_ineligible_for_every_use():
    """Verify that all 120 holdout episodes remain universally ineligible across every pipeline route."""
    assert QREC_PATH.is_file()
    qrec = json.loads(QREC_PATH.read_text(encoding="utf-8"))

    assert qrec["status"] == COMPROMISED_HOLDOUT_STATUS
    assert qrec["holdout_id"] == COMPROMISED_HOLDOUT_ID
    assert qrec["affected_episodes"]["total_count"] == 120

    flags = qrec["eligibility_flags"]
    assert flags["blind_evaluation_eligible"] is False
    assert flags["training_eligible"] is False
    assert flags["validation_eligible"] is False
    assert flags["calibration_eligible"] is False
    assert flags["checkpoint_selection_eligible"] is False
    assert flags["final_evaluation_eligible"] is False
    assert flags["tuning_eligible"] is False
    assert flags["model_selection_eligible"] is False
    assert flags["benchmark_eligible"] is False
    assert flags["holdout_eligible"] is False

    # Target loader must fail closed on holdout root
    with pytest.raises(HoldoutIneligibleError):
        AuditTargetLoader(S35_HOLDOUT)

    # Evaluator must fail closed on holdout evaluation requests
    evaluator = HCTTReadOnlyEvaluator(target_loader=None)
    with pytest.raises(HoldoutIneligibleError):
        evaluator.verify_holdout_eligibility(COMPROMISED_HOLDOUT_ID)

    with pytest.raises(HoldoutIneligibleError):
        evaluator.evaluate_final_holdout(
            model=None,
            dataset=None,
            target_loader=None,
            holdout_credentials={"certified_private_holdout": True},
        )
