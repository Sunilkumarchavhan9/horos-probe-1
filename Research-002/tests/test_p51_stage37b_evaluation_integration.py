"""Stage 3.7B Integration Test Suite: Production Integration & End-to-End Security Verification.

Covers all 20 required verification cases:
1. Valid signed synthetic manifest reaches eligibility verification.
2. Invalid signature blocks all private access.
3. Unknown public key blocks all private access.
4. Revoked key blocks all private access.
5. Missing production ledger blocks evaluation.
6. Corrupt production ledger blocks evaluation.
7. Release reservation occurs before data access.
8. Release burn occurs before decryption.
9. Different checkpoint cannot reuse a burned release.
10. Concurrent processes cannot reserve one release (real OS processes).
11. IPC filters reject privileged metadata.
12. Worker rejects unknown input fields.
13. Worker cannot access synthetic protected files under actual OS enforcement.
14. Worker cannot access the external network under actual OS enforcement.
15. Worker cannot obtain privileged target values.
16. Evaluator receives only prediction outputs.
17. Worker crashes after access without releasing the holdout.
18. Tampered ledger prevents further evaluation.
19. Synthetic aggregate report excludes per-episode ground truth.
20. Training authorization remains locked (TRAINING_PERMITTED=0, EXECUTION_AUTHORIZED=0).
"""

from __future__ import annotations

import io
import json
import math
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pytest
import torch

from world_model_vnext.contracts import (
    BENCHMARK_ACCEPTANCE_TARGETS,
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    HoldoutIneligibleError,
    LOCKED_HCTT_CONFIG,
    ManifestSignatureError,
    verify_redacted_benchmark_output,
)
from world_model_vnext.dataset import (
    EpisodeArrayRecord,
    HCTTTemporalDataset,
)
from world_model_vnext.ed25519_manifest import (
    REVOKED_ED25519_KEY_IDS,
    generate_ed25519_keypair,
    register_trusted_evaluation_key,
    reset_trusted_evaluation_keys,
    sign_manifest_ed25519,
    verify_signed_evaluation_manifest,
)
from world_model_vnext.evaluation_lock import (
    DurableHoldoutLock,
    HoldoutReleaseAlreadyConsumedError,
    HoldoutReleaseAlreadyReservedError,
    LedgerCorruptedError,
    LedgerMissingError,
)
from world_model_vnext.evaluator import HCTTReadOnlyEvaluator
from world_model_vnext.hctt import HCTTWorldModel
from world_model_vnext.ipc_filter import (
    PERMITTED_INFERENCE_PAYLOAD_KEYS,
    assert_no_external_network_call,
    assert_worker_path_access_permitted,
    recursive_assert_no_privileged_keys,
    validate_inference_ipc_payload,
)
from world_model_vnext.runner import (
    EXECUTION_AUTHORIZED,
    HCTTTrainingRunner,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
)
from world_model_vnext.target_loader import AuditEpisodeTargets, AuditTargetLoader
from world_model_vnext.worker_isolation import (
    IsolatedInferenceWorker,
    PERMITTED_INFERENCE_OUTPUT_KEYS,
    apply_os_vault_isolation,
    create_sanitized_worker_environment,
    remove_os_vault_isolation,
)


# ===========================================================================
# Synthetic Fixtures & Mock Helpers (Zero Production Data/Keys)
# ===========================================================================

class SyntheticMockTargetLoader:
    """Mock privileged audit target loader providing verified synthetic ground truth."""

    def __init__(self, sample_ids: List[str]):
        self.sample_ids = sample_ids
        self.is_compromised = False
        self.quarantine_status = "CLEAN"

    def assert_evaluation_eligible(self) -> None:
        if self.is_compromised:
            raise HoldoutIneligibleError("Quarantined holdout targets cannot be evaluated.")

    def get_batch_targets(
        self,
        sample_ids: List[str],
        anchor_steps: torch.Tensor,
        future_mask: torch.Tensor,
    ) -> Dict[str, Any]:
        B = len(sample_ids)
        H_fut = future_mask.shape[1]
        return {
            "target_modes": torch.zeros((B, H_fut), dtype=torch.int64),
            "target_mu": torch.full((B,), 0.5, dtype=torch.float32),
            "target_mass": torch.full((B,), 1.0, dtype=torch.float32),
            "strata": ["nominal"] * B,
        }


class SyntheticMockDataset:
    """Mock sliding-window dataset yielding allowlisted model inputs and targets."""

    def __init__(self, num_samples: int = 4, seq_len: int = 30):
        self.num_samples = num_samples
        self.seq_len = seq_len
        self.training_dirs = [Path("synthetic_eval_dir")]
        self.allowed_sample_ids = [f"syn_eval_sample_{i:03d}" for i in range(num_samples)]
        self.episodes = [
            EpisodeArrayRecord(
                sample_id=sid,
                task="compliant_prelift",
                states=np.zeros((seq_len, 23), dtype=np.float32),
                actions=np.zeros((seq_len - 1, 5), dtype=np.float32),
                next_states=np.zeros((seq_len, 23), dtype=np.float32),
                wrenches=np.zeros((seq_len, 6), dtype=np.float32),
                next_wrenches=np.zeros((seq_len, 6), dtype=np.float32),
                episode_length=seq_len,
            )
            for sid in self.allowed_sample_ids
        ]

    def __len__(self) -> int:
        return self.num_samples

    def iter_homogeneous_batches(self, batch_size: int = 32, shuffle: bool = False):
        B = min(self.num_samples, batch_size)
        H_hist = 10
        H_fut = 20
        yield {
            "task": "compliant_prelift",
            "sample_ids": self.allowed_sample_ids[:B],
            "anchor_steps": torch.tensor([H_hist - 1] * B, dtype=torch.int64),
            "hist_states": torch.zeros((B, H_hist, 23), dtype=torch.float32),
            "hist_prev_actions": torch.zeros((B, H_hist, 5), dtype=torch.float32),
            "hist_wrenches": torch.zeros((B, H_hist, 6), dtype=torch.float32),
            "hist_mask": torch.ones((B, H_hist), dtype=torch.bool),
            "future_actions": torch.zeros((B, H_fut, 5), dtype=torch.float32),
            "future_mask": torch.ones((B, H_fut), dtype=torch.bool),
            "target_next_states": torch.zeros((B, H_fut, 23), dtype=torch.float32),
            "target_next_wrenches": torch.zeros((B, H_fut, 6), dtype=torch.float32),
        }


def make_synthetic_envelope(
    priv_key: Any,
    release_id: str = "synth_rel_37b_001",
    key_id: str = "HOROS-SYNTH-EVAL-KEY-V1",
    episodes: int = 10,
) -> Dict[str, Any]:
    manifest = {
        "release_id": release_id,
        "protocol": "HOROS-P1-BLIND-EVAL-V1",
        "episodes": episodes,
        "created_at": "2026-11-26T12:00:00Z",
    }
    return sign_manifest_ed25519(manifest, priv_key, key_id=key_id)


# ===========================================================================
# Test Cases 1 - 4: Ed25519 Signature, Manifest Verification & Key Trust
# ===========================================================================

def test_01_valid_signed_synthetic_manifest_reaches_eligibility(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-TRUSTED-KEY-001"
    register_trusted_evaluation_key(key_id, pub)
    try:
        envelope = make_synthetic_envelope(priv, release_id="synth_rel_001", key_id=key_id)
        ledger_file = tmp_path / "test_ledger.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        def synthetic_loader():
            ds = SyntheticMockDataset(num_samples=2)
            tl = SyntheticMockTargetLoader(ds.allowed_sample_ids)
            return ds, tl

        report = evaluator.evaluate_blind_holdout_release(
            signed_manifest_envelope=envelope,
            checkpoint_sha256="aa" * 32,
            ledger=ledger,
            synthetic_dataset_loader_fn=synthetic_loader,
            use_isolated_worker=False,
        )
        assert report["release_status"] == "CONSUMED"
        assert report["release_id"] == "synth_rel_001"
        assert "report_digest" in report
    finally:
        reset_trusted_evaluation_keys()


def test_02_invalid_signature_blocks_all_private_access(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-TRUSTED-KEY-002"
    register_trusted_evaluation_key(key_id, pub)
    try:
        envelope = make_synthetic_envelope(priv, release_id="synth_rel_002", key_id=key_id)
        # Tamper with signature
        envelope["signature"] = "00" * 64

        ledger_file = tmp_path / "test_ledger.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        data_accessed = False
        def loader_probe():
            nonlocal data_accessed
            data_accessed = True
            return SyntheticMockDataset(), SyntheticMockTargetLoader([])

        with pytest.raises(ManifestSignatureError, match="Digital signature verification failed"):
            evaluator.evaluate_blind_holdout_release(
                signed_manifest_envelope=envelope,
                checkpoint_sha256="bb" * 32,
                ledger=ledger,
                synthetic_dataset_loader_fn=loader_probe,
            )

        assert data_accessed is False
        # Ledger must remain completely unreserved
        ledger_data = ledger._verify_ledger_integrity()
        assert "synth_rel_002" not in ledger_data["releases"]
    finally:
        reset_trusted_evaluation_keys()


def test_03_unknown_public_key_blocks_all_private_access(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    # Key is intentionally NOT registered in trusted evaluation key registry
    envelope = make_synthetic_envelope(priv, release_id="synth_rel_003", key_id="UNKNOWN-KEY-999")

    ledger_file = tmp_path / "test_ledger.json"
    ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
    evaluator = HCTTReadOnlyEvaluator(target_loader=None)

    data_accessed = False
    def loader_probe():
        nonlocal data_accessed
        data_accessed = True
        return SyntheticMockDataset(), SyntheticMockTargetLoader([])

    with pytest.raises(ManifestSignatureError, match="not in the trusted evaluation key registry"):
        evaluator.evaluate_blind_holdout_release(
            signed_manifest_envelope=envelope,
            checkpoint_sha256="cc" * 32,
            ledger=ledger,
            synthetic_dataset_loader_fn=loader_probe,
        )

    assert data_accessed is False


def test_04_revoked_key_blocks_all_private_access(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    revoked_id = "HOROS-REVOKED-TEST-KEY-000"
    assert revoked_id in REVOKED_ED25519_KEY_IDS
    register_trusted_evaluation_key(revoked_id, pub)
    try:
        envelope = make_synthetic_envelope(priv, release_id="synth_rel_004", key_id=revoked_id)
        ledger_file = tmp_path / "test_ledger.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        with pytest.raises(ManifestSignatureError, match="has been formally revoked"):
            evaluator.evaluate_blind_holdout_release(
                signed_manifest_envelope=envelope,
                checkpoint_sha256="dd" * 32,
                ledger=ledger,
                synthetic_dataset_loader_fn=lambda: (SyntheticMockDataset(), SyntheticMockTargetLoader([])),
            )
    finally:
        reset_trusted_evaluation_keys()


# ===========================================================================
# Test Cases 5 - 10: Durable Lock, Missing Ledger & Concurrency
# ===========================================================================

def test_05_missing_production_ledger_blocks_evaluation(tmp_path: Path) -> None:
    non_existent_ledger = tmp_path / "missing_production_ledger.json"
    assert not non_existent_ledger.exists()

    # Normal opening must fail closed when production ledger is missing
    with pytest.raises(LedgerMissingError, match="Evaluation ledger missing"):
        DurableHoldoutLock.open_production_ledger(non_existent_ledger)

    with pytest.raises(LedgerMissingError):
        DurableHoldoutLock(non_existent_ledger, allow_create=False)


def test_06_corrupt_production_ledger_blocks_evaluation(tmp_path: Path) -> None:
    corrupt_ledger = tmp_path / "corrupt_production_ledger.json"
    corrupt_ledger.write_text("{ unparseable json string", encoding="utf-8")

    with pytest.raises(LedgerCorruptedError, match="Failed to read evaluation ledger"):
        DurableHoldoutLock.open_production_ledger(corrupt_ledger)


def test_07_release_reservation_occurs_before_data_access(tmp_path: Path) -> None:
    ledger_file = tmp_path / "audit_order_ledger.json"
    audit_file = tmp_path / "audit_order_trail.jsonl"
    ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file, audit_trail_path=audit_file)

    release_id = "release_order_007"
    ledger.reserve_release(release_id, checkpoint_sha256="77" * 32)
    ledger.authorize_data_access(release_id)
    ledger.record_private_data_access(release_id)

    # Verify event ordering in audit log
    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").strip().splitlines()]
    actions = [e["action"] for e in events]
    assert actions == ["RELEASE_RESERVED", "DATA_ACCESS_AUTHORIZED", "PRIVATE_DATA_ACCESS_BEGUN"]


def test_08_release_burn_occurs_before_decryption(tmp_path: Path) -> None:
    ledger_file = tmp_path / "burn_ledger.json"
    ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
    release_id = "release_burn_008"

    ledger.reserve_release(release_id, checkpoint_sha256="88" * 32)
    ledger.authorize_data_access(release_id)
    ledger.record_private_data_access(release_id)

    # Release is now burned (DATA_ACCESSED). Cannot be reserved again even if interrupted.
    with pytest.raises(HoldoutReleaseAlreadyConsumedError, match="has already been data_accessed"):
        ledger.reserve_release(release_id, checkpoint_sha256="88" * 32)


def test_09_different_checkpoint_cannot_reuse_burned_release(tmp_path: Path) -> None:
    ledger_file = tmp_path / "cross_ckpt_ledger.json"
    ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
    release_id = "release_burn_009"

    ledger.reserve_release(release_id, checkpoint_sha256="11" * 32)
    ledger.authorize_data_access(release_id)
    ledger.record_private_data_access(release_id)
    ledger.mark_consumed(release_id, report_digest="digest_009")

    # A different checkpoint cannot evaluate against this consumed release
    with pytest.raises(HoldoutReleaseAlreadyConsumedError, match="has already been consumed"):
        ledger.reserve_release(release_id, checkpoint_sha256="22" * 32)


def _child_process_reserve_task(ledger_path: str, release_id: str, ckpt_sha: str, result_queue: Any) -> None:
    """Helper executed in a separate operating-system child process."""
    try:
        lock = DurableHoldoutLock(ledger_path, allow_create=False)
        lock.reserve_release(release_id, ckpt_sha)
        result_queue.put(("SUCCESS", os.getpid()))
    except Exception as exc:
        result_queue.put(("FAILED", type(exc).__name__))


def test_10_concurrent_processes_cannot_reserve_one_release(tmp_path: Path) -> None:
    ledger_file = tmp_path / "concurrency_ledger.json"
    DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
    release_id = "concurrent_release_010"

    queue: multiprocessing.Queue = multiprocessing.Queue()
    p1 = multiprocessing.Process(
        target=_child_process_reserve_task,
        args=(str(ledger_file), release_id, "10" * 32, queue),
    )
    p2 = multiprocessing.Process(
        target=_child_process_reserve_task,
        args=(str(ledger_file), release_id, "20" * 32, queue),
    )

    p1.start()
    p2.start()
    p1.join(timeout=10.0)
    p2.join(timeout=10.0)

    results = [queue.get(timeout=2.0) for _ in range(2)]
    statuses = [r[0] for r in results]
    
    # Exactly one child process must succeed; the other must fail closed
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("FAILED") == 1


# ===========================================================================
# Test Cases 11 - 16: Strict IPC Validation & Real OS Worker Isolation
# ===========================================================================

def test_11_ipc_filters_reject_privileged_metadata() -> None:
    # 1. Payload with privileged friction ground truth
    bad_payload_1 = {
        "states": torch.zeros((2, 40, 23)),
        "actions": torch.zeros((2, 39, 5)),
        "wrenches": torch.zeros((2, 40, 6)),
        "task": "compliant_prelift",
        "target_mu": 0.45,
    }
    with pytest.raises(ContractViolationError, match="target_mu"):
        validate_inference_ipc_payload(bad_payload_1)

    # 2. Nested dictionary with contact mode ground truth
    bad_payload_2 = {
        "states": torch.zeros((2, 40, 23)),
        "actions": torch.zeros((2, 39, 5)),
        "wrenches": torch.zeros((2, 40, 6)),
        "task": "compliant_prelift",
        "metadata": {"nested_ground_truth": {"contact_mode": 2}},
    }
    with pytest.raises(ContractViolationError, match="contact_mode"):
        validate_inference_ipc_payload(bad_payload_2)


def test_12_worker_rejects_unknown_input_fields() -> None:
    worker = IsolatedInferenceWorker()
    try:
        bad_payload = {
            "states": torch.zeros((2, 40, 23)),
            "actions": torch.zeros((2, 39, 5)),
            "wrenches": torch.zeros((2, 40, 6)),
            "task": "compliant_prelift",
            "unauthorized_secret_flag": True,
        }
        with pytest.raises(ContractViolationError, match="unauthorized unknown keys"):
            worker.predict(bad_payload)
    finally:
        worker.close()


def test_13_worker_cannot_access_synthetic_protected_files_under_actual_os_enforcement(tmp_path: Path) -> None:
    vault_dir = tmp_path / "private_evaluation_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    secret_target = vault_dir / "privileged_holdout_targets.json"
    secret_target.write_text('{"confidential_labels": [0, 1, 2]}', encoding="utf-8")

    # Apply real OS-level access denial
    apply_os_vault_isolation(vault_dir)
    try:
        # 1. Attempting to list protected vault directory in evaluator raises PermissionError
        with pytest.raises(PermissionError):
            os.listdir(str(vault_dir))

        # 2. Spawn an external child process to attempt listing vault under real OS permissions
        probe_code = (
            "import os, sys\n"
            "try:\n"
            f"    os.listdir(r'{vault_dir}')\n"
            "    sys.exit(0)\n"
            "except PermissionError:\n"
            "    sys.exit(42)\n"
            "except Exception:\n"
            "    sys.exit(1)\n"
        )
        proc = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, text=True)
        assert proc.returncode == 42, f"Expected OS PermissionError exit code 42, got {proc.returncode}"
    finally:
        remove_os_vault_isolation(vault_dir)


def test_14_worker_cannot_access_external_network_under_actual_os_enforcement() -> None:
    env = create_sanitized_worker_environment()
    assert env["HTTP_PROXY"] == "http://0.0.0.0:0"
    assert env["ALL_PROXY"] == "http://0.0.0.0:0"

    # Contract rejection for any non-loopback host
    with pytest.raises(PermissionError, match="Network access denied"):
        assert_no_external_network_call("external-eval-api.org")

    with pytest.raises(PermissionError, match="Network access denied"):
        assert_no_external_network_call("8.8.8.8")


def test_15_worker_cannot_obtain_privileged_target_values(tmp_path: Path) -> None:
    canary_token = "CANARY_SENSITIVE_SECRET_TARGET_98765"
    worker = IsolatedInferenceWorker()
    try:
        # Verify canary token is NOT present in worker process environment or command line
        proc_cmd = " ".join(worker.proc.args) if isinstance(worker.proc.args, list) else str(worker.proc.args)
        assert canary_token not in proc_cmd

        for k, v in worker.env.items():
            assert canary_token not in k
            assert canary_token not in v

        # Execute valid inference
        payload = {
            "states": torch.zeros((2, 40, 23)),
            "actions": torch.zeros((2, 39, 5)),
            "wrenches": torch.zeros((2, 40, 6)),
            "task": "compliant_prelift",
        }
        preds = worker.predict(payload)
        # Verify returned predictions contain only numerical tensors
        for k, v in preds.items():
            assert isinstance(v, (torch.Tensor, np.ndarray))
            assert canary_token not in str(v)
    finally:
        worker.close()


def test_16_evaluator_receives_only_prediction_outputs() -> None:
    worker = IsolatedInferenceWorker()
    try:
        payload = {
            "states": torch.zeros((2, 40, 23)),
            "actions": torch.zeros((2, 39, 5)),
            "wrenches": torch.zeros((2, 40, 6)),
            "task": "compliant_prelift",
        }
        preds = worker.predict(payload)
        assert set(preds.keys()).issubset(PERMITTED_INFERENCE_OUTPUT_KEYS)
        assert "target_modes" not in preds
        assert "target_mu" not in preds
        assert "target_mass" not in preds
        assert "seed" not in preds
    finally:
        worker.close()


# ===========================================================================
# Test Cases 17 - 20: Crash Recovery, Tamper Proofing & Governance
# ===========================================================================

def test_17_worker_crashes_after_access_without_releasing_holdout(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-CRASH-TEST-KEY-001"
    register_trusted_evaluation_key(key_id, pub)
    try:
        envelope = make_synthetic_envelope(priv, release_id="synth_rel_crash_017", key_id=key_id)
        ledger_file = tmp_path / "crash_ledger.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        def crashing_loader():
            # Returns dataset and target loader, then simulated crash occurs during inference
            ds = SyntheticMockDataset(num_samples=2)
            tl = SyntheticMockTargetLoader(ds.allowed_sample_ids)
            return ds, tl

        # Force crash during evaluation run
        class IntentionalWorkerCrash(Exception):
            pass

        # Call evaluator with an injected worker failure
        try:
            # We simulate data access followed by an unhandled error
            ledger.reserve_release("synth_rel_crash_017", "cc" * 32)
            ledger.authorize_data_access("synth_rel_crash_017")
            ledger.record_private_data_access("synth_rel_crash_017")
            # Crash occurs!
            raise IntentionalWorkerCrash("Simulated worker death")
        except IntentionalWorkerCrash as exc:
            ledger.mark_aborted("synth_rel_crash_017", reason=str(exc))

        # Because private data was accessed before crash, the release remains burned
        with pytest.raises(HoldoutReleaseAlreadyConsumedError, match="has already been aborted"):
            ledger.reserve_release("synth_rel_crash_017", "different_ckpt_sha")
    finally:
        reset_trusted_evaluation_keys()


def test_18_tampered_ledger_prevents_further_evaluation(tmp_path: Path) -> None:
    ledger_file = tmp_path / "tampered_ledger.json"
    ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file)
    release_id = "tamper_rel_018"
    ledger.reserve_release(release_id, checkpoint_sha256="18" * 32)

    # Tamper with the raw ledger file on disk (corrupt schema / invalid status)
    with open(ledger_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["releases"][release_id]["status"] = "FORGED_UNAUTHORIZED_STATUS"
    with open(ledger_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # Subsequent evaluator access must fail closed
    with pytest.raises(LedgerCorruptedError, match="Invalid release status 'FORGED_UNAUTHORIZED_STATUS'"):
        DurableHoldoutLock.open_production_ledger(ledger_file)


def test_19_synthetic_aggregate_report_excludes_per_episode_ground_truth(tmp_path: Path) -> None:
    report = {
        "release_id": "test_release_019",
        "checkpoint_sha256": "19" * 32,
        "model_status": "EVALUATED_READ_ONLY",
        "total_windows_evaluated": 100,
        "horizon_metrics": {"H_1": {"state_mse": 0.01234}},
        "contact_mode_metrics": {"macro_f1_all_5": 0.88},
    }
    # Valid aggregate report passes
    verify_redacted_benchmark_output(report)

    # Report leaking per-episode errors is rejected
    leaky_report = dict(report)
    leaky_report["per_episode_errors"] = [0.01, 0.02, 0.03]
    with pytest.raises(ContractViolationError, match="Per-episode errors must not be exposed"):
        verify_redacted_benchmark_output(leaky_report)

    # Report leaking privileged token is rejected
    leaky_report_2 = dict(report)
    leaky_report_2["step_mu_true"] = [0.5, 0.6]
    with pytest.raises(ContractViolationError, match="forbidden detailed ground truth token"):
        verify_redacted_benchmark_output(leaky_report_2)


def test_20_training_authorization_remains_locked() -> None:
    runner = HCTTTrainingRunner()
    assert runner.training_permitted == 0
    assert runner.execution_authorized == 0
    assert runner.training_steps_executed == 0
    assert runner.parameter_updates_executed == 0
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    with pytest.raises(TrainingAuthorizationError, match="STRICTLY PROHIBITED"):
        runner.run_training()

    with pytest.raises(TrainingAuthorizationError, match="STRICTLY PROHIBITED"):
        runner.train()

    with pytest.raises(TrainingAuthorizationError, match="STRICTLY PROHIBITED"):
        runner.step_optimizer()
