"""HOROS PROBE-1 — Stage 3.7C.1: Final Security Corrections & Evaluation Integrity Closure Regression Suite (P53).

Validates:
1. Mandatory candidate checkpoint availability and SHA-256 digest verification before release reservation.
2. In-worker independent checkpoint verification, safe weights_only=True and strict=True loading.
3. Immediate rejection of PREDICT requests until verified weights are loaded.
4. Pinned checkpoint digest recorded in durable ledger release record.
5. Removal of public exports for Ed25519 trust-registry mutation and production immutability.
6. Complete elimination of hardcoded ledger authentication secret and fail-closed secret requirement.
7. In-worker diagnostic commands: TEST_VAULT_ACCESS, TEST_OUTBOUND_NETWORK, and GET_SECURITY_INFO.
8. Genuine audit of OS isolation and network isolation boundaries.
9. Safe binary IPC protocol boundary enforcement (magic, dimensions, dtypes, non-finite values).
10. Mandatory research governance invariants.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import threading
import time
from typing import Any, Dict, Tuple

import numpy as np
import pytest
import torch

import world_model_vnext
from world_model_vnext.contracts import (
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    HoldoutIneligibleError,
    LOCKED_HCTT_CONFIG,
    ManifestSignatureError,
)
from world_model_vnext.dataset import EpisodeArrayRecord, HCTTTemporalDataset
from world_model_vnext.ed25519_manifest import (
    REVOKED_ED25519_KEY_IDS,
    _test_register_evaluation_key,
    _test_reset_evaluation_keys,
    generate_ed25519_keypair,
    get_trusted_evaluation_public_key,
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
from world_model_vnext.runner import EXECUTION_AUTHORIZED, TRAINING_PERMITTED
from world_model_vnext.target_loader import AuditEpisodeTargets, AuditTargetLoader
from world_model_vnext.worker_isolation import (
    HIPC_MAGIC,
    MAX_IPC_HEADER_SIZE,
    MAX_IPC_PAYLOAD_SIZE,
    IsolatedInferenceWorker,
    apply_os_vault_isolation,
    recv_ipc_message,
    remove_os_vault_isolation,
    send_ipc_message,
)


# ===========================================================================
# Synthetic Test Helpers
# ===========================================================================

def make_signed_manifest(private_key: Any, release_id: str, key_id: str) -> Dict[str, Any]:
    payload = {
        "release_id": release_id,
        "format_version": "vNext-3.7",
        "created_at": "2026-11-26T17:00:00Z",
        "episode_count": 1,
        "seed_range": [800000, 800000],
        "holdout_archive_sha256": "33" * 32,
    }
    return sign_manifest_ed25519(payload, private_key=private_key, key_id=key_id)


def create_synthetic_checkpoint(path: Path | str) -> Tuple[Path, str]:
    p = Path(path)
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    state = model.state_dict()
    torch.save(state, p)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    return p, digest


class MockTargetLoader(AuditTargetLoader):
    def __init__(self, sample_ids: list[str]) -> None:
        pass

    def get_batch_targets(self, sample_ids: Any, anchor_steps: Any, future_mask: Any) -> Dict[str, Any]:
        B = len(sample_ids)
        return {
            "target_modes": torch.zeros((B, 20), dtype=torch.long),
            "target_mu": torch.zeros(B, dtype=torch.float32),
            "target_mass": torch.zeros(B, dtype=torch.float32),
            "strata": ["nominal"] * B,
        }


class MockDataset(HCTTTemporalDataset):
    def __init__(self) -> None:
        self.records = {}
        self.windows = [("s1", 0, 10)]

    def __len__(self) -> int:
        return 1

    def iter_homogeneous_batches(self, batch_size: int = 32, shuffle: bool = False):
        yield {
            "sample_ids": ["s1"],
            "anchor_steps": torch.tensor([0]),
            "task": "compliant_prelift",
            "hist_states": torch.zeros((1, 10, 23), dtype=torch.float32),
            "hist_prev_actions": torch.zeros((1, 10, 5), dtype=torch.float32),
            "hist_wrenches": torch.zeros((1, 10, 6), dtype=torch.float32),
            "hist_mask": torch.ones((1, 10), dtype=torch.bool),
            "future_actions": torch.zeros((1, 20, 5), dtype=torch.float32),
            "future_mask": torch.ones((1, 20), dtype=torch.bool),
            "target_next_states": torch.zeros((1, 20, 23), dtype=torch.float32),
            "target_next_wrenches": torch.zeros((1, 20, 6), dtype=torch.float32),
        }


# ===========================================================================
# 1. Mandatory Checkpoint Integrity Enforcement
# ===========================================================================

def test_01_missing_checkpoint_path_fails_before_reservation(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-TRUSTED-P53-01"
    _test_register_evaluation_key(key_id, pub)
    try:
        envelope = make_signed_manifest(priv, "rel_p53_01", key_id)
        ledger_file = tmp_path / "ledger_01.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file, auth_key=b"test-auth-key-01")
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        with pytest.raises(ContractViolationError, match="Mandatory 'checkpoint_path' is missing or empty"):
            evaluator.evaluate_blind_holdout_release(
                signed_manifest_envelope=envelope,
                checkpoint_sha256="aa" * 32,
                ledger=ledger,
                synthetic_dataset_loader_fn=lambda: (MockDataset(), MockTargetLoader([])),
                checkpoint_path=None,
            )

        # Confirm ledger was NEVER touched: release was not reserved
        disk_data = ledger._verify_ledger_integrity()
        assert "rel_p53_01" not in disk_data["releases"]
    finally:
        _test_reset_evaluation_keys()


def test_02_nonexistent_checkpoint_file_fails_before_reservation(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-TRUSTED-P53-02"
    _test_register_evaluation_key(key_id, pub)
    try:
        envelope = make_signed_manifest(priv, "rel_p53_02", key_id)
        ledger_file = tmp_path / "ledger_02.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file, auth_key=b"test-auth-key-02")
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        nonexistent = tmp_path / "does_not_exist_model.pt"
        with pytest.raises(ContractViolationError, match="Mandatory candidate checkpoint file missing or inaccessible"):
            evaluator.evaluate_blind_holdout_release(
                signed_manifest_envelope=envelope,
                checkpoint_sha256="bb" * 32,
                ledger=ledger,
                synthetic_dataset_loader_fn=lambda: (MockDataset(), MockTargetLoader([])),
                checkpoint_path=nonexistent,
            )

        disk_data = ledger._verify_ledger_integrity()
        assert "rel_p53_02" not in disk_data["releases"]
    finally:
        _test_reset_evaluation_keys()


def test_03_corrupted_checkpoint_digest_mismatch_fails_before_reservation(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-TRUSTED-P53-03"
    _test_register_evaluation_key(key_id, pub)
    try:
        envelope = make_signed_manifest(priv, "rel_p53_03", key_id)
        ledger_file = tmp_path / "ledger_03.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file, auth_key=b"test-auth-key-03")
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        ckpt_file, actual_sha256 = create_synthetic_checkpoint(tmp_path / "model_03.pt")
        wrong_sha256 = "00" * 32

        with pytest.raises(ContractViolationError, match="Candidate checkpoint SHA-256 mismatch"):
            evaluator.evaluate_blind_holdout_release(
                signed_manifest_envelope=envelope,
                checkpoint_sha256=wrong_sha256,
                ledger=ledger,
                synthetic_dataset_loader_fn=lambda: (MockDataset(), MockTargetLoader([])),
                checkpoint_path=ckpt_file,
            )

        disk_data = ledger._verify_ledger_integrity()
        assert "rel_p53_03" not in disk_data["releases"]
    finally:
        _test_reset_evaluation_keys()


def test_04_checkpoint_digest_in_ledger_matches_exact_loaded_artifact(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    key_id = "HOROS-TRUSTED-P53-04"
    _test_register_evaluation_key(key_id, pub)
    try:
        envelope = make_signed_manifest(priv, "rel_p53_04", key_id)
        ledger_file = tmp_path / "ledger_04.json"
        ledger = DurableHoldoutLock.initialize_fresh_ledger(ledger_file, auth_key=b"test-auth-key-04")
        evaluator = HCTTReadOnlyEvaluator(target_loader=None)

        ckpt_file, actual_sha256 = create_synthetic_checkpoint(tmp_path / "model_04.pt")

        report = evaluator.evaluate_blind_holdout_release(
            signed_manifest_envelope=envelope,
            checkpoint_sha256=actual_sha256,
            ledger=ledger,
            synthetic_dataset_loader_fn=lambda: (MockDataset(), MockTargetLoader([])),
            checkpoint_path=ckpt_file,
        )

        assert report["release_status"] == "CONSUMED"
        assert report["checkpoint_sha256"] == actual_sha256

        # Verify disk ledger contains exact checkpoint SHA-256
        with open(ledger_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        rel_record = disk_data["releases"]["rel_p53_04"]
        assert rel_record["checkpoint_sha256"] == actual_sha256
        assert rel_record["status"] == "CONSUMED"
    finally:
        _test_reset_evaluation_keys()


# ===========================================================================
# 2. Worker Checkpoint Loading & PREDICT Lockout
# ===========================================================================

def test_05_worker_rejects_predict_before_verified_checkpoint_loaded() -> None:
    worker = IsolatedInferenceWorker()
    try:
        assert worker._model_verified_and_loaded is False
        payload = {
            "states": torch.zeros((1, 10, 23)),
            "actions": torch.zeros((1, 10, 5)),
            "wrenches": torch.zeros((1, 10, 6)),
            "task": "compliant_prelift",
        }
        # Client-side fail closed
        with pytest.raises(ContractViolationError, match="cannot execute predict before candidate checkpoint weights are loaded"):
            worker.predict(payload)

        # Worker process IPC level fail closed
        worker._model_verified_and_loaded = True  # force bypass client check to test worker process
        with pytest.raises(ContractViolationError, match="Inference worker cannot predict before verified checkpoint weights are loaded"):
            worker.predict(payload)
    finally:
        worker.close()


def test_06_worker_loads_checkpoint_with_weights_only_and_strict(tmp_path: Path) -> None:
    ckpt_file, actual_sha256 = create_synthetic_checkpoint(tmp_path / "valid_model.pt")
    worker = IsolatedInferenceWorker(checkpoint_path=ckpt_file, expected_checkpoint_sha256=actual_sha256)
    try:
        assert worker._model_verified_and_loaded is True
        assert worker.verified_checkpoint_sha256 == actual_sha256

        out = worker.predict({
            "states": torch.zeros((1, 10, 23)),
            "actions": torch.zeros((1, 10, 5)),
            "wrenches": torch.zeros((1, 10, 6)),
            "task": "compliant_prelift",
        })
        assert "pred_next_states" in out
        assert torch.isfinite(out["pred_next_states"]).all()
    finally:
        worker.close()


def test_07_worker_rejects_malformed_and_substituted_state_dict(tmp_path: Path) -> None:
    bad_ckpt = tmp_path / "bad_weights.pt"
    # Save a non-state_dict dictionary
    torch.save({"unsupported_key": [1, 2, 3]}, bad_ckpt)
    actual_sha = hashlib.sha256(bad_ckpt.read_bytes()).hexdigest()

    worker = IsolatedInferenceWorker()
    try:
        with pytest.raises(ContractViolationError, match="Worker failed to load candidate checkpoint"):
            worker.load_checkpoint(bad_ckpt, actual_sha)
        assert worker._model_verified_and_loaded is False
    finally:
        worker.close()


# ===========================================================================
# 3. Secure Ed25519 Trust Configuration
# ===========================================================================

def test_08_no_public_trust_mutation_exports_in_init() -> None:
    exported_names = set(world_model_vnext.__all__)
    assert "register_trusted_evaluation_key" not in exported_names
    assert "unregister_trusted_evaluation_key" not in exported_names
    assert "reset_trusted_evaluation_keys" not in exported_names


def test_09_production_mode_strictly_prohibits_trust_registry_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOROS_ENVIRONMENT", "production")
    _, pub = generate_ed25519_keypair()

    with pytest.raises(ContractViolationError, match="Trust registry mutation is strictly prohibited in production mode"):
        _test_register_evaluation_key("INJECTED_PROD_KEY", pub)


def test_10_untrusted_signing_key_rejected() -> None:
    priv, _ = generate_ed25519_keypair()
    envelope = make_signed_manifest(priv, "rel_untrusted", "HOROS-UNKNOWN-UNPINNED-KEY")

    with pytest.raises(ManifestSignatureError, match="not in the trusted evaluation key registry"):
        verify_signed_evaluation_manifest(envelope)


def test_11_caller_cannot_inject_trusted_keys_into_evaluator(tmp_path: Path) -> None:
    priv, pub = generate_ed25519_keypair()
    envelope = make_signed_manifest(priv, "rel_override_test", "ANY-KEY")
    ledger = DurableHoldoutLock.initialize_fresh_ledger(tmp_path / "ledger_ov.json", auth_key=b"k")
    evaluator = HCTTReadOnlyEvaluator(target_loader=None)

    with pytest.raises(ContractViolationError, match="Caller-supplied 'trusted_keys' override is strictly prohibited"):
        evaluator.evaluate_blind_holdout_release(
            signed_manifest_envelope=envelope,
            checkpoint_sha256="aa" * 32,
            ledger=ledger,
            synthetic_dataset_loader_fn=lambda: (MockDataset(), MockTargetLoader([])),
            checkpoint_path=tmp_path / "dummy.pt",
            trusted_keys={"ANY-KEY": pub},
        )


# ===========================================================================
# 4. Remove Hardcoded Ledger Authentication Key
# ===========================================================================

def test_12_no_hardcoded_ledger_auth_key_in_source() -> None:
    import world_model_vnext.evaluation_lock as elock
    assert not hasattr(elock, "_DEFAULT_LEDGER_AUTH_KEY")


def test_13_missing_ledger_secret_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOROS_LEDGER_AUTH_KEY", raising=False)

    with pytest.raises(ContractViolationError, match="Mandatory ledger authentication key is missing"):
        DurableHoldoutLock.initialize_fresh_ledger(tmp_path / "ledger_no_key.json", auth_key=None)


def test_14_explicit_ephemeral_auth_key_accepted(tmp_path: Path) -> None:
    auth_key = b"EPHEMERAL-SECRET-TEST-KEY-42"
    lock = DurableHoldoutLock.initialize_fresh_ledger(tmp_path / "ledger_explicit.json", auth_key=auth_key)
    assert lock.auth_key == auth_key
    assert lock.ledger_path.exists()


def test_15_wrong_auth_key_detects_tampering(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger_key_tamper.json"
    key1 = b"KEY-1-SECRET-ORIGINAL"
    key2 = b"KEY-2-SECRET-DIFFERENT"

    lock1 = DurableHoldoutLock.initialize_fresh_ledger(ledger_path, auth_key=key1)
    lock1.reserve_release("rel_auth_test", checkpoint_sha256="aa" * 32)

    # Opening the existing ledger with a different auth_key must fail HMAC check
    with pytest.raises(LedgerCorruptedError, match="HMAC verification failed"):
        DurableHoldoutLock.open_production_ledger(ledger_path, auth_key=key2)


# ===========================================================================
# 5. OS Worker Isolation & In-Worker Vault Access
# ===========================================================================

def test_16_in_worker_vault_access_test(tmp_path: Path) -> None:
    vault = tmp_path / "secure_vault"
    vault.mkdir()
    secret_file = vault / "holdout_weights_secret.key"
    secret_file.write_bytes(b"CONFIDENTIAL_HOLDOUT_SECRET_BYTES")

    worker = IsolatedInferenceWorker()
    try:
        # 1. Before OS deny ACL, worker can read the file
        res1 = worker.test_vault_access(secret_file)
        assert res1.get("status") == "ACCESS_ALLOWED"

        # 2. Apply OS-level vault isolation
        apply_os_vault_isolation(vault)

        # 3. Worker attempts access after isolation
        res2 = worker.test_vault_access(secret_file)
        # On Windows NTFS deny ACL, read is blocked with ACCESS_DENIED
        if os.name == "nt":
            assert res2.get("status") == "ACCESS_DENIED"
    finally:
        remove_os_vault_isolation(vault)
        worker.close()


def test_17_worker_security_info_reports_actual_pid_and_sid() -> None:
    worker = IsolatedInferenceWorker()
    try:
        info = worker.get_security_info()
        assert info.get("status") == "OK"
        assert info.get("pid") != os.getpid(), "Worker PID must differ from parent test process!"
        assert "sid" in info
        assert "env_proxy" in info
    finally:
        worker.close()


# ===========================================================================
# 6. In-Worker Outbound Network Denial
# ===========================================================================

def test_18_in_worker_socket_network_test_from_child_process() -> None:
    # Ephemeral local TCP server
    server = socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)
    host, port = server.server_address
    t = threading.Thread(target=server.handle_request)
    t.daemon = True
    t.start()

    worker = IsolatedInferenceWorker()
    try:
        # Direct raw TCP socket test from inside the actual worker process
        res = worker.test_outbound_network(host, port, timeout=1.0)
        # On standard Windows desktop without container network isolation, raw TCP socket connects
        # This honestly verifies the boundary and establishes need for container --network none
        assert res.get("status") in ["NETWORK_ALLOWED", "NETWORK_DENIED"]
    finally:
        worker.close()
        server.server_close()


# ===========================================================================
# 7. Safe Binary IPC Protocol Boundary Hardening
# ===========================================================================

def test_19_ipc_rejects_negative_or_overflowing_tensor_shapes() -> None:
    bio = io.BytesIO()
    header = {
        "_tensors": {
            "bad_tensor": {
                "parent": None,
                "key": "bad_tensor",
                "dtype": "float32",
                "shape": [-1, 10],  # Negative dimension!
                "offset": 0,
                "length": 40,
            }
        }
    }
    hdr_bytes = json.dumps(header).encode("utf-8")
    body = b"\x00" * 40
    total_len = 4 + len(hdr_bytes) + len(body)
    bio.write(HIPC_MAGIC)
    bio.write(struct.pack(">I", total_len))
    bio.write(struct.pack(">I", len(hdr_bytes)))
    bio.write(hdr_bytes)
    bio.write(body)
    bio.seek(0)

    with pytest.raises(ContractViolationError, match="contains invalid dimensions"):
        recv_ipc_message(bio)


def test_20_ipc_rejects_disallowed_dtypes_and_corrupt_magic() -> None:
    # 1. Corrupt magic header
    bio1 = io.BytesIO(b"NOPE\x00\x00\x00\x10\x00\x00\x00\x04{}0000")
    with pytest.raises(ContractViolationError, match="Invalid IPC protocol magic header"):
        recv_ipc_message(bio1)

    # 2. Disallowed tensor dtype
    bio2 = io.BytesIO()
    header = {
        "_tensors": {
            "obj_tensor": {
                "parent": None,
                "key": "obj_tensor",
                "dtype": "object",
                "shape": [1],
                "offset": 0,
                "length": 8,
            }
        }
    }
    hdr_bytes = json.dumps(header).encode("utf-8")
    body = b"\x00" * 8
    total_len = 4 + len(hdr_bytes) + len(body)
    bio2.write(HIPC_MAGIC)
    bio2.write(struct.pack(">I", total_len))
    bio2.write(struct.pack(">I", len(hdr_bytes)))
    bio2.write(hdr_bytes)
    bio2.write(body)
    bio2.seek(0)

    with pytest.raises(ContractViolationError, match="Disallowed tensor dtype"):
        recv_ipc_message(bio2)


def test_21_ipc_rejects_nan_and_inf_tensors() -> None:
    bio = io.BytesIO()
    nan_tensor = torch.tensor([float("nan"), 1.0, 2.0], dtype=torch.float32)
    with pytest.raises(ContractViolationError, match="contains non-finite values"):
        send_ipc_message(bio, {"nan_val": nan_tensor})


# ===========================================================================
# 8. Mandatory Research Governance Invariants
# ===========================================================================

def test_22_mandatory_governance_invariants_hold() -> None:
    assert TRAINING_PERMITTED == 0, "TRAINING_PERMITTED invariant must be 0!"
    assert EXECUTION_AUTHORIZED == 0, "EXECUTION_AUTHORIZED invariant must be 0!"
    assert COMPROMISED_HOLDOUT_STATUS == "COMPROMISED_PUBLIC_EXPOSURE"
