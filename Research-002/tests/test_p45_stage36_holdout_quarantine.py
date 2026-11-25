"""HOROS PROBE-1 — Research-002: Stage 3.6 Blind Holdout Quarantine & Evaluation Integrity Tests.

Verifies the 13 mandatory security and quarantine requirements for Stage 3.6:
1. The old Stage 3.5 holdout is classified as compromised.
2. The final evaluator rejects it.
3. Rejection happens before trajectory archive loading.
4. Training-data discovery cannot select it.
5. Validation-data discovery cannot select it.
6. Calibration cannot select it.
7. Checkpoint selection cannot consume it.
8. Alternative manifest aliases cannot bypass the guard.
9. No silent fallback to the old holdout is possible.
10. The train, validation, and calibration splits remain unchanged.
11. Training authorization remains disabled.
12. The locked objective configuration remains unchanged.
13. Existing temporal-window and dataset-integrity protections still pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set
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
    COMPROMISED_HOLDOUT_SEED_RANGES,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    EXECUTION_AUTHORIZED,
    EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG,
    HCTTMultiObjectiveLoss,
    HCTTReadOnlyEvaluator,
    HCTTTemporalDataset,
    HCTTTrainingRunner,
    HCTTWorldModel,
    HoldoutIneligibleError,
    LOCKED_HCTT_CONFIG,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
    assert_not_compromised_holdout_data,
    assert_split_eligible_for_evaluation,
    get_compromised_sample_ids,
    is_compromised_holdout_path,
    is_compromised_holdout_sample_id,
    is_compromised_holdout_seed,
    load_clean_training_episode,
    verify_split_manifest_disjointness,
)

S33_ROOT = R002_ROOT / "experiments" / "stage3_production"
S35_ROOT = R002_ROOT / "experiments" / "stage35_production"
S35_PRELIFT_LONG = S35_ROOT / "prelift_long"
S35_FINAL_HOLDOUT = S35_ROOT / "final_holdout"
S36_QUARANTINE_DIR = R002_ROOT / "experiments" / "stage36_holdout_quarantine"
QREC_PATH = S36_QUARANTINE_DIR / "holdout_quarantine_record.json"
S35_SPLIT_MANIFEST_PATH = S35_ROOT / "stage35_final_split_manifest.json"


# ---------------------------------------------------------------------------
# 1. Old Stage 3.5 holdout is classified as compromised
# ---------------------------------------------------------------------------

def test_old_stage35_holdout_classified_as_compromised() -> None:
    """Requirement 1: Verify explicit quarantine status and metadata for Stage 3.5 holdout."""
    assert COMPROMISED_HOLDOUT_ID == "stage35_final_holdout"
    assert COMPROMISED_HOLDOUT_STATUS == "COMPROMISED_PUBLIC_EXPOSURE"
    assert COMPROMISED_HOLDOUT_PROTOCOL == "HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT"

    assert QREC_PATH.is_file(), f"Quarantine record missing at {QREC_PATH}"
    qrec = json.loads(QREC_PATH.read_text(encoding="utf-8"))

    assert qrec["quarantine_id"] == "HOROS-P1-R002-STAGE36-QREC-01"
    assert qrec["status"] == "COMPROMISED_PUBLIC_EXPOSURE"
    assert qrec["holdout_id"] == "stage35_final_holdout"
    assert qrec["affected_episodes"]["total_count"] == 120
    assert qrec["affected_episodes"]["compliant_prelift_count"] == 60
    assert qrec["affected_episodes"]["peg_in_hole_count"] == 60

    # Verify all eligibility flags are explicitly False
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

    # Verify seed and sample_id membership
    compromised_sids = get_compromised_sample_ids()
    assert len(compromised_sids) == 120

    for sid in qrec["affected_episodes"]["sample_ids"]:
        assert is_compromised_holdout_sample_id(sid) is True

    for range_name, (start, end) in COMPROMISED_HOLDOUT_SEED_RANGES.items():
        for s in range(start, end + 1):
            assert is_compromised_holdout_seed(s) is True


# ---------------------------------------------------------------------------
# 2. Final evaluator rejects compromised holdout
# ---------------------------------------------------------------------------

def test_final_evaluator_rejects_compromised_holdout() -> None:
    """Requirement 2: Evaluator must fail closed with explicit exception on compromised holdout."""
    target_loader = AuditTargetLoader(S33_ROOT)
    evaluator = HCTTReadOnlyEvaluator(target_loader=target_loader)

    # Rejection by identifier
    with pytest.raises(HoldoutIneligibleError) as exc_info:
        evaluator.verify_holdout_eligibility("stage35_final_holdout")
    assert "quarantined" in str(exc_info.value).lower()
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)

    # Rejection via evaluate_final_holdout without certified credentials
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    mock_dataset = MagicMock(spec=HCTTTemporalDataset)
    mock_dataset.training_dirs = [Path("some_clean_dir")]
    mock_dataset.allowed_sample_ids = None
    mock_dataset.episodes = []

    with pytest.raises(HoldoutIneligibleError):
        evaluator.evaluate_final_holdout(
            model=model,
            dataset=mock_dataset,
            target_loader=target_loader,
            holdout_credentials={"holdout_id": "stage35_final_holdout"},
        )

    # Rejection if credentials lack certified_private_holdout flag
    with pytest.raises(HoldoutIneligibleError):
        evaluator.evaluate_final_holdout(
            model=model,
            dataset=mock_dataset,
            target_loader=target_loader,
            holdout_credentials={"holdout_id": "unverified_private_set"},
        )


# ---------------------------------------------------------------------------
# 3. Rejection happens before trajectory archive loading
# ---------------------------------------------------------------------------

def test_rejection_happens_before_trajectory_archive_loading() -> None:
    """Requirement 3: Rejection occurs in __init__ before opening any .npz files."""
    holdout_training_dir = S35_FINAL_HOLDOUT / "training"
    assert holdout_training_dir.is_dir()

    # Must raise CompromisedHoldoutError without reading archives
    with patch("numpy.load") as mock_np_load:
        with pytest.raises(CompromisedHoldoutError) as exc_info:
            HCTTTemporalDataset(holdout_training_dir)
        # Verify numpy.load was never called
        mock_np_load.assert_not_called()

    assert "matches quarantined dataset 'stage35_final_holdout'" in str(exc_info.value)
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Training-data discovery cannot select it
# ---------------------------------------------------------------------------

def test_training_data_discovery_cannot_select_compromised_holdout() -> None:
    """Requirement 4: Training dataloaders refuse compromised holdout directories and archives."""
    holdout_training_dir = S35_FINAL_HOLDOUT / "training"

    # ProductionTrainingDataloader must refuse the directory
    with pytest.raises(SecurityViolationError) as exc_info:
        ProductionTrainingDataloader(holdout_training_dir)
    assert "Quarantined compromised holdout directory rejected" in str(exc_info.value)

    # Attempting to load any single archive through clean loader must fail
    sample_npz = next(holdout_training_dir.glob("*.npz"))
    with pytest.raises(CompromisedHoldoutError):
        load_clean_training_episode(sample_npz)


# ---------------------------------------------------------------------------
# 5. Validation-data discovery cannot select it
# ---------------------------------------------------------------------------

def test_validation_data_discovery_cannot_select_compromised_holdout() -> None:
    """Requirement 5: Dataset construction fails closed if allowed_sample_ids includes holdout IDs."""
    compromised_sids = list(get_compromised_sample_ids())[:3]
    clean_training_dir = S35_PRELIFT_LONG / "training"

    # Supplying compromised sample IDs to allowed_sample_ids must raise immediately
    with pytest.raises(CompromisedHoldoutError) as exc_info:
        HCTTTemporalDataset(
            training_dir=clean_training_dir,
            allowed_sample_ids=compromised_sids,
        )
    assert "allowed_sample_ids contains quarantined sample ID" in str(exc_info.value)
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Calibration cannot select it
# ---------------------------------------------------------------------------

def test_calibration_cannot_select_compromised_holdout() -> None:
    """Requirement 6: Split manifests forbid evaluation selection of final_test or holdout seeds."""
    manifest = json.loads(S35_SPLIT_MANIFEST_PATH.read_text(encoding="utf-8"))

    # final_test must be explicitly rejected
    with pytest.raises(CompromisedHoldoutError) as exc_info:
        assert_split_eligible_for_evaluation(manifest, "final_test")
    assert "permanently ineligible for evaluation" in str(exc_info.value)

    # Valid calibration split must pass
    assert_split_eligible_for_evaluation(manifest, "calib")

    # Contaminated manifest with holdout seed in calib must fail
    bad_manifest = {
        "splits": {
            "calib_contaminated": {
                "sample_ids": ["clean_sample_abc"],
                "seeds": [3531000],  # Compromised holdout seed
            }
        }
    }
    with pytest.raises(CompromisedHoldoutError) as exc_info2:
        assert_split_eligible_for_evaluation(bad_manifest, "calib_contaminated")
    assert "contains quarantined seed '3531000'" in str(exc_info2.value)


# ---------------------------------------------------------------------------
# 7. Checkpoint selection cannot consume it
# ---------------------------------------------------------------------------

def test_checkpoint_selection_cannot_consume_compromised_holdout() -> None:
    """Requirement 7: Frozen baseline evaluation and model selection paths reject compromised holdout."""
    target_loader = AuditTargetLoader(S33_ROOT)
    evaluator = HCTTReadOnlyEvaluator(target_loader=target_loader)

    # Mock dataset with compromised path in training_dirs
    mock_bad_dataset = MagicMock(spec=HCTTTemporalDataset)
    mock_bad_dataset.training_dirs = [S35_FINAL_HOLDOUT / "training"]
    mock_bad_dataset.allowed_sample_ids = None
    mock_bad_dataset.episodes = []

    mock_baseline_model = MagicMock()

    with pytest.raises(CompromisedHoldoutError) as exc_info:
        evaluator.evaluate_frozen_baseline_on_dataset(
            hybrid_model=mock_baseline_model,
            dataset=mock_bad_dataset,
            task="compliant_prelift",
        )
    assert "belongs to quarantined holdout 'stage35_final_holdout'" in str(exc_info.value)

    # Also test evaluate_strict_vs_masked rejection
    mock_hctt = MagicMock(spec=HCTTWorldModel)
    with pytest.raises(CompromisedHoldoutError):
        evaluator.evaluate_strict_vs_masked(
            model=mock_hctt,
            dataset=mock_bad_dataset,
            task="compliant_prelift",
        )


# ---------------------------------------------------------------------------
# 8. Alternative manifest aliases cannot bypass guard
# ---------------------------------------------------------------------------

def test_alternative_manifest_aliases_cannot_bypass_guard(tmp_path: Path) -> None:
    """Requirement 8: Renamed directories or spoofed filenames are rejected via sample_id fingerprinting."""
    compromised_sids = list(get_compromised_sample_ids())
    target_sid = compromised_sids[0]

    # Create a spoofed/renamed directory with an archive containing a compromised sample_id
    spoofed_dir = tmp_path / "innocent_dataset_dir"
    spoofed_dir.mkdir(parents=True, exist_ok=True)
    spoofed_archive = spoofed_dir / "clean_episode_000.npz"

    # Write a valid archive containing the compromised sample_id
    np.savez(
        spoofed_archive,
        states=np.zeros((20, 23), dtype=np.float32),
        actions=np.zeros((20, 5), dtype=np.float32),
        next_states=np.zeros((20, 23), dtype=np.float32),
        wrenches=np.zeros((20, 6), dtype=np.float32),
        next_wrenches=np.zeros((20, 6), dtype=np.float32),
        sample_id=target_sid,
        task="compliant_prelift",
    )

    # Direct loading must reject via sample_id
    with pytest.raises(CompromisedHoldoutError) as exc_info:
        load_clean_training_episode(spoofed_archive)
    assert f"Sample ID '{target_sid}' belongs to quarantined dataset 'stage35_final_holdout'" in str(exc_info.value)

    # Dataset loading must reject when parsing episodes
    with pytest.raises(CompromisedHoldoutError):
        HCTTTemporalDataset(spoofed_dir)


# ---------------------------------------------------------------------------
# 9. No silent fallback to old holdout is possible
# ---------------------------------------------------------------------------

def test_no_silent_fallback_to_old_holdout() -> None:
    """Requirement 9: Attempting to point AuditTargetLoader at final_holdout fails closed during construction."""
    with pytest.raises(HoldoutIneligibleError) as exc_info:
        AuditTargetLoader(S35_FINAL_HOLDOUT)
    assert "quarantined" in str(exc_info.value).lower()
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 10. Train, validation, and calibration splits remain unchanged
# ---------------------------------------------------------------------------

def test_train_val_calib_splits_remain_unchanged() -> None:
    """Requirement 10: Preserves Stage 3.5 train (698), val (126), and calib (126) splits intact."""
    manifest = json.loads(S35_SPLIT_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert verify_split_manifest_disjointness(manifest) is True
    splits = manifest["splits"]

    # Invariant partition sizes
    assert splits["train"]["num_episodes"] == 698
    assert splits["val"]["num_episodes"] == 126
    assert splits["calib"]["num_episodes"] == 126
    assert splits["exploratory_baseline_quarantined"]["num_episodes"] == 100
    assert splits["legacy_short_horizon_preserved"]["num_episodes"] == 450
    assert manifest["total_episodes_accounted"] == 1620

    # Ensure zero overlap between clean partitions and compromised holdout seeds
    compromised_sids = get_compromised_sample_ids()
    for clean_part in ("train", "val", "calib"):
        clean_sids = set(splits[clean_part]["sample_ids"])
        clean_seeds = set(splits[clean_part]["seeds"])
        assert clean_sids.isdisjoint(compromised_sids), f"{clean_part} overlaps compromised sample_ids"
        for seed in clean_seeds:
            assert is_compromised_holdout_seed(seed) is False, f"{clean_part} contains holdout seed {seed}"


# ---------------------------------------------------------------------------
# 11. Training authorization remains disabled
# ---------------------------------------------------------------------------

def test_training_authorization_remains_disabled() -> None:
    """Requirement 11: Governance constants remain 0; runner rejects training and compromised inputs."""
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    runner = HCTTTrainingRunner()
    runner.assert_training_locked()

    with pytest.raises(TrainingAuthorizationError):
        runner.train()

    with pytest.raises(TrainingAuthorizationError):
        runner.step_optimizer()

    with pytest.raises(TrainingAuthorizationError):
        runner.backward()

    # verify_forward_and_loss_no_grad must reject compromised sample IDs
    compromised_sid = list(get_compromised_sample_ids())[0]
    bad_model_inputs = {"sample_id": compromised_sid}
    with pytest.raises(CompromisedHoldoutError) as exc_info:
        runner.verify_forward_and_loss_no_grad(
            model_inputs=bad_model_inputs,
            sim_targets={},
            audit_targets={},
        )
    assert f"Sample ID '{compromised_sid}' belongs to quarantined holdout" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 12. Locked objective configuration remains unchanged
# ---------------------------------------------------------------------------

def test_locked_objective_configuration_remains_unchanged() -> None:
    """Requirement 12: Preserves 4-term locked weights and keeps 3-term objective unauthorized."""
    cfg = LOCKED_HCTT_CONFIG
    assert cfg.weight_sim_dynamics == 1.0
    assert cfg.weight_mode_focal == 0.5
    assert cfg.weight_sys_id == 0.25
    assert cfg.weight_distill == 0.10
    assert cfg.objective_authorized_for_training is False

    exp_cfg = EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG
    assert exp_cfg.weight_distill == 0.0
    assert exp_cfg.objective_authorized_for_training is False


# ---------------------------------------------------------------------------
# 13. Existing temporal-window and dataset-integrity protections pass
# ---------------------------------------------------------------------------

def test_existing_temporal_window_and_dataset_integrity_pass() -> None:
    """Requirement 13: Clean Stage 3.5 prelift_long dataset loads and produces valid strict windows."""
    clean_dir = S35_PRELIFT_LONG / "training"
    assert clean_dir.is_dir()

    # Must load successfully without any CompromisedHoldoutError
    dataset = HCTTTemporalDataset(
        clean_dir,
        task_filter="compliant_prelift",
        window_mode="strict_unpadded",
        min_future_steps=1,
    )
    assert len(dataset) > 0
    sample = dataset[0]
    assert sample["valid_hist_len"] == 10
    assert not is_compromised_holdout_sample_id(sample["sample_id"])
    assert not is_compromised_holdout_path(clean_dir)
