"""Phase H: Synthetic-only protocol tests for Stage 3.7 Independent Blind Evaluation Protocol.

Verifies:
- Synthetic manifest schema and digital signature verification.
- Tamper-detection on manifest and archive payloads (fail-closed on single-byte corruption).
- Key stripping and evaluator process isolation contracts (24 forbidden privileged keys).
- Redacted aggregate benchmark report verification (blocks per-episode ground truth leakage).
- One-time evaluation lock enforcement (rejects repeated queries on identical holdout).
- Governance invariance: TRAINING_PERMITTED == 0, EXECUTION_AUTHORIZED == 0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Set, Tuple

import pytest

from world_model_vnext.contracts import (
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    EvaluationAlreadyExecutedError,
    FORBIDDEN_MODEL_INPUT_KEYS,
    ManifestSignatureError,
    assert_no_privileged_keys_in_inputs,
    assert_not_compromised_holdout_data,
    compute_manifest_signature,
    enforce_one_time_evaluation_lock,
    strip_forbidden_model_input_keys,
    verify_holdout_archive_digests,
    verify_redacted_benchmark_output,
    verify_signed_manifest_signature,
)
from world_model_vnext.runner import (
    EXECUTION_AUTHORIZED,
    HCTTTrainingRunner,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
)


# ===========================================================================
# Test Group 1: Synthetic Manifest & Cryptographic Signature Verification
# ===========================================================================

def test_01_synthetic_signed_manifest_valid_signature_passes() -> None:
    synthetic_manifest = {
        "manifest_version": "HOROS-STAGE37-SYNTHETIC-V1",
        "episode_count": 2,
        "task": "compliant_prelift",
        "archives": {
            "ep001.npz": "a" * 64,
            "ep002.npz": "b" * 64,
        },
    }
    manifest_bytes = json.dumps(synthetic_manifest, sort_keys=True).encode("utf-8")
    test_key = b"synthetic_evaluation_test_key_32b!"
    signature = compute_manifest_signature(manifest_bytes, test_key)

    is_valid = verify_signed_manifest_signature(manifest_bytes, signature, test_key)
    assert is_valid is True


def test_02_synthetic_manifest_single_byte_mutation_fails_signature() -> None:
    manifest_bytes = b'{"manifest_version": "V1", "episodes": 240}'
    test_key = b"synthetic_evaluation_test_key_32b!"
    signature = compute_manifest_signature(manifest_bytes, test_key)

    tampered_bytes = b'{"manifest_version": "V1", "episodes": 241}'
    is_valid = verify_signed_manifest_signature(tampered_bytes, signature, test_key)
    assert is_valid is False


def test_03_synthetic_manifest_wrong_verification_key_fails() -> None:
    manifest_bytes = b'{"protocol": "HOROS-P1-STAGE37"}'
    signing_key = b"correct_custodian_signing_key_32!"
    attacker_key = b"attacker_unauthorized_key_32bytes"
    signature = compute_manifest_signature(manifest_bytes, signing_key)

    is_valid = verify_signed_manifest_signature(manifest_bytes, signature, attacker_key)
    assert is_valid is False


def test_04_synthetic_manifest_malformed_hex_signature_fails_closed() -> None:
    manifest_bytes = b'{"protocol": "HOROS-P1-STAGE37"}'
    test_key = b"synthetic_evaluation_test_key_32b!"
    
    # Passing an invalid non-hex string or incompatible key should handle securely
    is_valid = verify_signed_manifest_signature(manifest_bytes, "invalid_non_hex_sig!", test_key)
    assert is_valid is False


# ===========================================================================
# Test Group 2: Archive Streaming Hashes & Tamper Detection
# ===========================================================================

def test_05_archive_digests_table_validates_exact_payloads() -> None:
    payload1 = b"synthetic_npz_archive_payload_episode_1"
    payload2 = b"synthetic_npz_archive_payload_episode_2"

    manifest_data = {
        "archives": {
            "ep01.npz": hashlib.sha256(payload1).hexdigest(),
            "ep02.npz": hashlib.sha256(payload2).hexdigest(),
        }
    }
    archives_by_name = {
        "ep01.npz": payload1,
        "ep02.npz": payload2,
    }

    # Must pass cleanly with zero exceptions
    verify_holdout_archive_digests(manifest_data, archives_by_name)


def test_06_archive_digests_table_detects_corrupted_archive() -> None:
    payload1 = b"authentic_payload_episode_1"
    tampered_payload = b"tampered_payload_episode_1"

    manifest_data = {
        "archives": {
            "ep01.npz": hashlib.sha256(payload1).hexdigest(),
        }
    }
    archives_by_name = {
        "ep01.npz": tampered_payload,
    }

    with pytest.raises(ContractViolationError, match="Archive digest mismatch for 'ep01.npz'"):
        verify_holdout_archive_digests(manifest_data, archives_by_name)


def test_07_archive_digests_table_missing_archive_fails_closed() -> None:
    manifest_data = {
        "archives": {
            "ep01.npz": "c" * 64,
            "ep02.npz": "d" * 64,
        }
    }
    archives_by_name = {
        "ep01.npz": b"dummy_content",
    }

    with pytest.raises(ContractViolationError, match="Archive count mismatch"):
        verify_holdout_archive_digests(manifest_data, archives_by_name)


def test_08_archive_digests_table_unexpected_archive_fails_closed() -> None:
    manifest_data = {
        "archives": {
            "ep01.npz": hashlib.sha256(b"p1").hexdigest(),
        }
    }
    archives_by_name = {
        "ep01.npz": b"p1",
        "ep02_unauthorized.npz": b"p2",
    }

    with pytest.raises(ContractViolationError, match="Archive count mismatch"):
        verify_holdout_archive_digests(manifest_data, archives_by_name)


# ===========================================================================
# Test Group 3: Evaluator Isolation & Key Stripping
# ===========================================================================

def test_09_strip_forbidden_model_input_keys_removes_all_privileged_keys() -> None:
    dirty_batch = {
        "states": [1.0, 2.0, 3.0],
        "actions": [0.1, 0.2],
        "wrenches": [0.0, 0.0, 5.0, 0.0, 0.0, 0.0],
        "task": "compliant_prelift",
        # Privileged targets to be stripped
        "modes": [1, 2, 2],
        "target_modes": [1, 2, 2],
        "target_mu": 0.55,
        "step_mu_true": [0.55, 0.55, 0.55],
        "mass_kg": 0.25,
        "object_mass": 0.25,
        "clearance_mm": 0.5,
        "seed": 1234567,
        "episode_id": "ep_042",
        "rewards": [0.0, 1.0],
        "step_contact_stratum": "CP-S1",
    }

    clean_batch = strip_forbidden_model_input_keys(dirty_batch)

    # Allowed keys preserved
    assert "states" in clean_batch
    assert "actions" in clean_batch
    assert "wrenches" in clean_batch
    assert "task" in clean_batch

    # Forbidden keys stripped
    for forbidden in FORBIDDEN_MODEL_INPUT_KEYS:
        assert forbidden not in clean_batch

    # Passes assertion
    assert_no_privileged_keys_in_inputs(clean_batch)


def test_10_assert_no_privileged_keys_rejects_subtle_privileged_tokens() -> None:
    with pytest.raises(ContractViolationError):
        assert_no_privileged_keys_in_inputs({"step_contact_stratum": "nominal"})

    with pytest.raises(ContractViolationError, match="matches forbidden privileged token"):
        assert_no_privileged_keys_in_inputs({"subtle_mu_true_value": 0.5})

    with pytest.raises(ContractViolationError, match="matches forbidden privileged token"):
        assert_no_privileged_keys_in_inputs({"hidden_privileged_mass_kg": 0.3})


# ===========================================================================
# Test Group 4: One-Time Evaluation Principle & Lockout
# ===========================================================================

def test_11_one_time_evaluation_lock_permits_first_evaluation() -> None:
    consumed_ledger: Set[Tuple[str, str]] = set()
    ckpt_sha = "3a7b8e11" * 8
    holdout_sha = "9f2c1d22" * 8

    # Must succeed on first try
    enforce_one_time_evaluation_lock(consumed_ledger, ckpt_sha, holdout_sha)

    # Register pair as consumed
    consumed_ledger.add((ckpt_sha, holdout_sha))
    assert (ckpt_sha, holdout_sha) in consumed_ledger


def test_12_one_time_evaluation_lock_rejects_repeated_evaluation() -> None:
    ckpt_sha = "3a7b8e11" * 8
    holdout_sha = "9f2c1d22" * 8
    consumed_ledger: Set[Tuple[str, str]] = {(ckpt_sha, holdout_sha)}

    with pytest.raises(EvaluationAlreadyExecutedError, match="has already been evaluated"):
        enforce_one_time_evaluation_lock(consumed_ledger, ckpt_sha, holdout_sha)


def test_13_one_time_evaluation_lock_allows_distinct_candidate_or_manifest() -> None:
    ckpt_sha1 = "11111111" * 8
    ckpt_sha2 = "22222222" * 8
    holdout_sha1 = "aaaaaaaa" * 8
    holdout_sha2 = "bbbbbbbb" * 8

    consumed_ledger: Set[Tuple[str, str]] = {(ckpt_sha1, holdout_sha1)}

    # Different candidate on new holdout is permitted
    enforce_one_time_evaluation_lock(consumed_ledger, ckpt_sha2, holdout_sha2)

    # Same candidate on new holdout is permitted
    enforce_one_time_evaluation_lock(consumed_ledger, ckpt_sha1, holdout_sha2)


# ===========================================================================
# Test Group 5: Redacted Benchmark Reporting & Anti-Leakage
# ===========================================================================

def test_14_verify_redacted_benchmark_output_passes_on_aggregate_report() -> None:
    valid_public_report = {
        "protocol_version": "HOROS-P1-STAGE37-EVAL-V1",
        "checkpoint_sha256": "3a7b8e" * 10,
        "multi_horizon_mse": {
            "h1": 0.0018,
            "h5": 0.0042,
            "h20": 0.0091,
        },
        "mse_reduction_vs_b3": 0.435,
        "contact_mode_macro_f1": 0.812,
        "latent_friction_r2": 0.641,
        "latent_mass_r2_dynamic": 0.615,
        "ood_discrimination_auroc": 0.884,
        "conformal_coverage_90": 0.908,
        "bootstrap_ci_95": {
            "macro_f1": [0.785, 0.839],
            "mse_reduction": [0.381, 0.489],
        },
        "gate_status": "ALL_GATES_PASSED",
    }

    # Must pass without raising
    verify_redacted_benchmark_output(valid_public_report)


def test_15_verify_redacted_benchmark_output_rejects_raw_per_episode_targets() -> None:
    leaky_report = {
        "gate_status": "PASSED",
        "step_mu_true": [0.5, 0.6, 0.7],  # Privileged detail leaked!
    }

    with pytest.raises(ContractViolationError, match="contains forbidden detailed ground truth token"):
        verify_redacted_benchmark_output(leaky_report)


def test_16_verify_redacted_benchmark_output_rejects_raw_per_episode_errors() -> None:
    leaky_report = {
        "gate_status": "PASSED",
        "per_episode_errors": [0.001, 0.002, 0.005],  # Forbidden per-episode disclosure!
    }

    with pytest.raises(ContractViolationError, match="Per-episode errors must not be exposed"):
        verify_redacted_benchmark_output(leaky_report)


# ===========================================================================
# Test Group 6: Governance Invariance & Research Boundaries
# ===========================================================================

def test_17_governance_invariance_training_permitted_is_zero() -> None:
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0


def test_18_governance_invariance_hctt_training_runner_raises_on_train_call() -> None:
    runner = HCTTTrainingRunner()
    with pytest.raises(TrainingAuthorizationError, match="Operation 'train' is STRICTLY PROHIBITED"):
        runner.train()
    with pytest.raises(TrainingAuthorizationError, match="Operation 'run_training' is STRICTLY PROHIBITED"):
        runner.run_training()


def test_19_governance_invariance_compromised_holdout_path_rejected() -> None:
    with pytest.raises(CompromisedHoldoutError, match="belongs to quarantined dataset"):
        assert_not_compromised_holdout_data("experiments/stage35_production/final_holdout/ep_001.npz")


def test_20_protocol_documents_exist_and_cover_all_eight_deliverables() -> None:
    base_dir = Path(__file__).resolve().parent.parent / "experiments" / "stage37_blind_evaluation"
    expected_docs = [
        "stage37_research_audit.md",
        "stage37_blind_evaluation_protocol.md",
        "stage37_holdout_generation_spec.md",
        "stage37_security_and_custody_model.md",
        "stage37_statistical_analysis_plan.md",
        "stage37_evaluation_runbook.md",
        "stage37_stage4_gate_matrix.md",
        "stage37_acceptance_report.md",
    ]
    for doc in expected_docs:
        doc_path = base_dir / doc
        assert doc_path.is_file(), f"Stage 3.7 deliverable missing: {doc_path}"
        assert doc_path.stat().st_size > 1000, f"Stage 3.7 deliverable appears incomplete: {doc_path}"
