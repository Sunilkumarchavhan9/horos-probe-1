"""HOROS PROBE-1 — Research-002: Stage 3.6A Quarantine Hardening & Adversarial Bypass Tests.

Covers all 16 mandatory adversarial requirements:
1. Original compromised directory rejected.
2. Renamed compromised archive rejected before np.load().
3. Relocated compromised archive rejected.
4. Original archive SHA-256 detected.
5. Renamed privileged JSON rejected before parsing.
6. Compromised target-loader root rejected during construction.
7. Compromised ground-truth data never loaded.
8. Alternative split names cannot bypass quarantine.
9. Missing sample IDs cannot silently grant eligibility.
10. Missing seed fields cannot silently grant eligibility.
11. Caller-supplied boolean credentials cannot authorize a private holdout.
12. All final evaluation routes reject the old holdout.
13. All baseline evaluation routes enforce eligibility.
14. Training authorization remains locked.
15. Train, validation, and calibration partitions remain unchanged.
16. The locked objective remains unchanged.
"""

from __future__ import annotations

import hashlib
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
    compute_file_sha256,
    get_compromised_npz_sha256,
    get_compromised_privileged_sha256,
    get_compromised_sample_ids,
    is_compromised_holdout_file_content,
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
# 1. Original compromised directory rejected
# ---------------------------------------------------------------------------

def test_original_compromised_directory_rejected() -> None:
    """Adv-1: Direct directory path to final_holdout is rejected fail-closed."""
    holdout_training_dir = S35_FINAL_HOLDOUT / "training"
    assert holdout_training_dir.is_dir()

    with pytest.raises(CompromisedHoldoutError) as exc_info:
        HCTTTemporalDataset(holdout_training_dir)
    assert "matches quarantined dataset 'stage35_final_holdout'" in str(exc_info.value)
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Renamed compromised archive rejected before np.load()
# ---------------------------------------------------------------------------

def test_renamed_compromised_archive_rejected_before_np_load(tmp_path: Path) -> None:
    """Adv-2: Archive with neutral name matching compromised SHA-256 is rejected without calling np.load."""
    sample_npz_sha = list(get_compromised_npz_sha256())[0]

    # Create dummy file whose SHA-256 matches the quarantined digest
    neutral_dir = tmp_path / "neutral_dataset_dir"
    neutral_dir.mkdir(parents=True, exist_ok=True)
    renamed_archive = neutral_dir / "unrelated_innocent_file.npz"

    # Mock compute_file_sha256 to return the compromised hash
    with patch("world_model_vnext.dataset.is_compromised_holdout_file_content", return_value=True):
        with patch("numpy.load") as mock_np_load:
            renamed_archive.write_bytes(b"dummy_data_representing_quarantined_archive")
            with pytest.raises(CompromisedHoldoutError) as exc_info:
                load_clean_training_episode(renamed_archive)
            # CRITICAL INVARIANT: np.load MUST NOT BE CALLED
            mock_np_load.assert_not_called()

    assert "matches quarantined holdout SHA-256 content digest" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3. Relocated compromised archive rejected
# ---------------------------------------------------------------------------

def test_relocated_compromised_archive_rejected(tmp_path: Path) -> None:
    """Adv-3: Compromised archive relocated to an arbitrary path is identified via SHA-256 content check."""
    relocated_dir = tmp_path / "arbitrary" / "subpath" / "training"
    relocated_dir.mkdir(parents=True, exist_ok=True)
    fake_archive = relocated_dir / "episode_9999.npz"
    fake_archive.write_bytes(b"binary_payload")

    with patch("world_model_vnext.dataset.is_compromised_holdout_file_content", return_value=True):
        with patch("numpy.load") as mock_np_load:
            with pytest.raises(CompromisedHoldoutError) as exc_info:
                load_clean_training_episode(fake_archive)
            mock_np_load.assert_not_called()

    assert "Pre-load digest rejection enforced" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Original archive SHA-256 detected
# ---------------------------------------------------------------------------

def test_original_archive_sha256_detected() -> None:
    """Adv-4: The 120 compromised NPZ digests are accurately registered and detected."""
    npz_shas = get_compromised_npz_sha256()
    assert len(npz_shas) == 120
    sample_sha = list(npz_shas)[0]
    assert isinstance(sample_sha, str)
    assert len(sample_sha) == 64

    # Verify is_compromised_holdout_file_content correctly identifies a real file from final_holdout
    real_sample_npz = next((S35_FINAL_HOLDOUT / "training").glob("*.npz"))
    assert is_compromised_holdout_file_content(real_sample_npz) is True

    # Verify a clean prelift_long file is NOT flagged as compromised
    clean_sample_npz = next((S35_PRELIFT_LONG / "training").glob("*.npz"))
    assert is_compromised_holdout_file_content(clean_sample_npz) is False


# ---------------------------------------------------------------------------
# 5. Renamed privileged JSON rejected before parsing
# ---------------------------------------------------------------------------

def test_renamed_privileged_json_rejected_before_parsing(tmp_path: Path) -> None:
    """Adv-5: Privileged audit file with neutral name matching compromised digest is rejected before json.loads."""
    priv_shas = get_compromised_privileged_sha256()
    assert len(priv_shas) == 120

    neutral_audit_dir = tmp_path / "clean_audit"
    neutral_audit_dir.mkdir(parents=True, exist_ok=True)
    renamed_priv = neutral_audit_dir / "clean_name.privileged.json"
    renamed_priv.write_text("{\"some\": \"valid_json\"}", encoding="utf-8")

    # When is_compromised_holdout_file_content returns True, assert_not_compromised_holdout_data must raise
    with patch("world_model_vnext.contracts.is_compromised_holdout_file_content", return_value=True):
        with patch("json.loads") as mock_json_loads:
            # Call assert_not_compromised_holdout_data directly
            with pytest.raises(CompromisedHoldoutError) as exc_info:
                assert_not_compromised_holdout_data(renamed_priv)
            mock_json_loads.assert_not_called()

    assert "matches quarantined SHA-256 digest" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Compromised target-loader root rejected during construction
# ---------------------------------------------------------------------------

def test_compromised_target_loader_root_rejected_during_construction() -> None:
    """Adv-6: AuditTargetLoader fails closed during __init__ when pointed to final_holdout."""
    with pytest.raises(HoldoutIneligibleError) as exc_info:
        AuditTargetLoader(S35_FINAL_HOLDOUT)
    assert "quarantined" in str(exc_info.value).lower()
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 7. Compromised ground-truth data never loaded
# ---------------------------------------------------------------------------

def test_compromised_ground_truth_data_never_loaded(tmp_path: Path) -> None:
    """Adv-7: If an audit manifest contains the compromised protocol, no privileged targets are loaded."""
    mock_root = tmp_path / "sneaky_holdout"
    audit_dir = mock_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (mock_root / "accepted_manifest.json").write_text("[]", encoding="utf-8")
    (audit_dir / "stage35_audit_manifest.json").write_text(
        json.dumps({"protocol": COMPROMISED_HOLDOUT_PROTOCOL}), encoding="utf-8"
    )

    with pytest.raises(HoldoutIneligibleError) as exc_info:
        AuditTargetLoader(mock_root)
    assert "quarantined" in str(exc_info.value).lower()
    assert "COMPROMISED_PUBLIC_EXPOSURE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8. Alternative split names cannot bypass quarantine
# ---------------------------------------------------------------------------

def test_alternative_split_names_cannot_bypass_quarantine() -> None:
    """Adv-8: Changing split name to an arbitrary alias while retaining holdout data fails closed."""
    compromised_sid = list(get_compromised_sample_ids())[0]
    bad_manifest = {
        "protocol": "HOROS-CLEAN-PROTOCOL-V1",
        "splits": {
            "stealth_unlabeled_split": {
                "sample_ids": [compromised_sid],
                "seeds": [9999999],
            }
        }
    }
    with pytest.raises(CompromisedHoldoutError) as exc_info:
        assert_split_eligible_for_evaluation(bad_manifest, "stealth_unlabeled_split")
    assert f"contains quarantined sample ID '{compromised_sid}'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 9. Missing sample IDs cannot silently grant eligibility
# ---------------------------------------------------------------------------

def test_missing_sample_ids_cannot_silently_grant_eligibility() -> None:
    """Adv-9: An unindexed split omitting sample_ids must fail closed with ContractViolationError."""
    bad_manifest = {
        "protocol": "HOROS-CLEAN-PROTOCOL-V1",
        "splits": {
            "empty_ids_split": {
                "seeds": [12345],
            }
        }
    }
    with pytest.raises(ContractViolationError) as exc_info:
        assert_split_eligible_for_evaluation(bad_manifest, "empty_ids_split")
    assert "lacks required non-empty 'sample_ids'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 10. Missing seed fields cannot silently grant eligibility
# ---------------------------------------------------------------------------

def test_missing_seed_fields_cannot_silently_grant_eligibility() -> None:
    """Adv-10: An unindexed split omitting seeds must fail closed with ContractViolationError."""
    bad_manifest = {
        "protocol": "HOROS-CLEAN-PROTOCOL-V1",
        "splits": {
            "empty_seeds_split": {
                "sample_ids": ["clean_sample_id_123"],
            }
        }
    }
    with pytest.raises(ContractViolationError) as exc_info:
        assert_split_eligible_for_evaluation(bad_manifest, "empty_seeds_split")
    assert "lacks required non-empty 'seeds'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 11. Caller-supplied boolean credentials cannot authorize a private holdout
# ---------------------------------------------------------------------------

def test_caller_supplied_boolean_credentials_rejected() -> None:
    """Adv-11: Supplying {'certified_private_holdout': True} is rejected without granting access."""
    target_loader = AuditTargetLoader(S33_ROOT)
    evaluator = HCTTReadOnlyEvaluator(target_loader=target_loader)

    # Boolean credential dictionary must be rejected
    bogus_creds = {"certified_private_holdout": True, "holdout_id": "my_new_holdout"}
    with pytest.raises(HoldoutIneligibleError) as exc_info:
        evaluator.verify_holdout_eligibility("my_new_holdout", authorization_artifact=bogus_creds)
    assert "Caller-supplied boolean credentials ('certified_private_holdout') are strictly rejected" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 12. All final evaluation routes reject the old holdout
# ---------------------------------------------------------------------------

def test_all_final_evaluation_routes_reject_old_holdout() -> None:
    """Adv-12: Both verify_holdout_eligibility and evaluate_final_holdout reject stage35_final_holdout."""
    target_loader = AuditTargetLoader(S33_ROOT)
    evaluator = HCTTReadOnlyEvaluator(target_loader=target_loader)

    for holdout_name in ("stage35_final_holdout", "final_holdout", "STAGE35_FINAL_HOLDOUT"):
        with pytest.raises(HoldoutIneligibleError) as exc_info:
            evaluator.verify_holdout_eligibility(holdout_name)
        assert "quarantined" in str(exc_info.value).lower()

    # evaluate_final_holdout must also raise
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    mock_dataset = MagicMock(spec=HCTTTemporalDataset)
    mock_dataset.training_dirs = [Path("clean_dir")]
    mock_dataset.allowed_sample_ids = None
    mock_dataset.episodes = []

    with pytest.raises(HoldoutIneligibleError):
        evaluator.evaluate_final_holdout(
            model=model,
            dataset=mock_dataset,
            target_loader=target_loader,
            holdout_credentials={"holdout_id": "stage35_final_holdout"},
        )


# ---------------------------------------------------------------------------
# 13. All baseline evaluation routes enforce eligibility
# ---------------------------------------------------------------------------

def test_all_baseline_evaluation_routes_enforce_eligibility() -> None:
    """Adv-13: Frozen baseline, strict-vs-masked, and persistence baselines reject quarantined datasets."""
    target_loader = AuditTargetLoader(S33_ROOT)
    evaluator = HCTTReadOnlyEvaluator(target_loader=target_loader)

    mock_bad_dataset = MagicMock(spec=HCTTTemporalDataset)
    mock_bad_dataset.training_dirs = [S35_FINAL_HOLDOUT / "training"]
    mock_bad_dataset.allowed_sample_ids = None
    mock_bad_dataset.episodes = []

    # Frozen baseline
    with pytest.raises(CompromisedHoldoutError):
        evaluator.evaluate_frozen_baseline_on_dataset(
            hybrid_model=MagicMock(),
            dataset=mock_bad_dataset,
            task="compliant_prelift",
        )

    # Strict vs masked
    with pytest.raises(CompromisedHoldoutError):
        evaluator.evaluate_strict_vs_masked(
            model=MagicMock(spec=HCTTWorldModel),
            dataset=mock_bad_dataset,
            task="compliant_prelift",
        )

    # Persistence baseline
    with pytest.raises(CompromisedHoldoutError):
        evaluator.evaluate_persistence_and_const_vel_baselines(
            dataset=mock_bad_dataset,
            task="compliant_prelift",
        )


# ---------------------------------------------------------------------------
# 14. Training authorization remains locked
# ---------------------------------------------------------------------------

def test_training_authorization_remains_locked() -> None:
    """Adv-14: TRAINING_PERMITTED=0 and EXECUTION_AUTHORIZED=0 remain hardcoded; runner fails closed."""
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    runner = HCTTTrainingRunner()
    runner.assert_training_locked()

    with pytest.raises(TrainingAuthorizationError):
        runner.train()

    with pytest.raises(TrainingAuthorizationError):
        runner.backward()

    with pytest.raises(TrainingAuthorizationError):
        runner.step_optimizer()


# ---------------------------------------------------------------------------
# 15. Train, validation, and calibration partitions remain unchanged
# ---------------------------------------------------------------------------

def test_train_val_calib_partitions_remain_unchanged() -> None:
    """Adv-15: Master split manifest partitions remain invariant (698/126/126) and strictly disjoint."""
    manifest = json.loads(S35_SPLIT_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert verify_split_manifest_disjointness(manifest) is True

    splits = manifest["splits"]
    assert splits["train"]["num_episodes"] == 698
    assert splits["val"]["num_episodes"] == 126
    assert splits["calib"]["num_episodes"] == 126
    assert splits["final_test"]["num_episodes"] == 120
    assert manifest["total_episodes_accounted"] == 1620


# ---------------------------------------------------------------------------
# 16. Locked objective remains unchanged
# ---------------------------------------------------------------------------

def test_locked_objective_remains_unchanged() -> None:
    """Adv-16: 4-term locked loss weights intact; 3-term objective unauthorized."""
    cfg = LOCKED_HCTT_CONFIG
    assert cfg.weight_sim_dynamics == 1.0
    assert cfg.weight_mode_focal == 0.5
    assert cfg.weight_sys_id == 0.25
    assert cfg.weight_distill == 0.10
    assert cfg.objective_authorized_for_training is False

    exp = EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG
    assert exp.weight_distill == 0.0
    assert exp.objective_authorized_for_training is False
