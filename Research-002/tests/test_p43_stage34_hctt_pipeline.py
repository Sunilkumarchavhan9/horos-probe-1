"""HOROS PROBE-1 — Research-002: Stage 3.4 HCTT-256 Implementation & Pipeline Tests.

Covers:
1. HCTT-256 architecture lock (6 layers, d_model=256, 8 heads, d_head=32, RoPE, task projections)
2. Input/target separation and zero privileged-field leakage enforcement
3. Causal masking (future actions a_{t+k} cannot influence history or steps < k; past actions causal)
4. Temporal window construction, explicit masking policy, and prediction target alignment
5. Separate AuditTargetLoader HMAC verification and fail-closed corruption handling
6. Episode split isolation and zero protected-seed overlap
7. Locked multi-objective loss calculation and strict distillation gating
8. Training authorization barrier enforcement (training_permitted=0, execution_authorized=0)
9. Deterministic batching, inference reproducibility, and untrained checkpoint save/reload
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

TESTS_DIR = Path(__file__).resolve().parent
R002_ROOT = TESTS_DIR.parent
for p in (R002_ROOT, R002_ROOT / "src"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)

from scripts.run_stage3_production_dataset import STAGE3_DETERMINISTIC_AUDIT_SALT  # noqa: E402
from world_model_vnext import (  # noqa: E402
    AuditTargetLoader,
    ContractViolationError,
    EXECUTION_AUTHORIZED,
    HCTTMultiObjectiveLoss,
    HCTTReadOnlyEvaluator,
    HCTTTemporalDataset,
    HCTTTrainingRunner,
    HCTTWorldModel,
    LOCKED_HCTT_CONFIG,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
    assert_no_privileged_keys_in_inputs,
    build_episode_split_manifest,
    collate_hctt_batch,
    split_batch_inputs_and_targets,
    verify_split_manifest_disjointness,
)

PROD_ROOT = R002_ROOT / "experiments" / "stage3_production"
TRAINING_DIR = PROD_ROOT / "training"


def test_authorization_barrier_strictly_enforced() -> None:
    """Verify training_permitted == 0, execution_authorized == 0, and all train methods fail closed."""
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    runner = HCTTTrainingRunner(seed=3400)
    with pytest.raises(TrainingAuthorizationError):
        runner.train()
    with pytest.raises(TrainingAuthorizationError):
        runner.run_training()
    with pytest.raises(TrainingAuthorizationError):
        runner.run_epoch()
    with pytest.raises(TrainingAuthorizationError):
        runner.step_optimizer()
    with pytest.raises(TrainingAuthorizationError):
        runner.backward()

    assert runner.training_steps_executed == 0
    assert runner.parameter_updates_executed == 0


def test_hctt_architecture_matches_locked_spec() -> None:
    """Verify locked HCTT-256 structural hyperparameters."""
    model = HCTTWorldModel()
    summary = model.parameter_summary()
    assert summary["d_model"] == 256
    assert summary["num_layers"] == 6
    assert summary["num_heads"] == 8
    assert summary["d_head"] == 32
    assert len(model.layers) == 6
    assert LOCKED_HCTT_CONFIG.history_horizon == 10
    assert LOCKED_HCTT_CONFIG.prediction_horizons == (1, 3, 5, 10, 20)
    assert LOCKED_HCTT_CONFIG.num_operational_modes == 5
    assert bool(model.trust_calibrated.item()) is False


def test_privileged_input_guard_rejects_leakage() -> None:
    """Verify model.forward and assert_no_privileged_keys_in_inputs fail closed on privileged keys."""
    for forbidden in ("modes", "target_modes", "friction_mu", "mu_true", "mass_kg", "clearance_mm", "rewards"):
        with pytest.raises(ContractViolationError):
            assert_no_privileged_keys_in_inputs({forbidden: torch.zeros(2)})

    model = HCTTWorldModel()
    B = 2
    with pytest.raises(ContractViolationError):
        model(
            task="compliant_prelift",
            hist_states=torch.zeros((B, 10, 23)),
            hist_prev_actions=torch.zeros((B, 10, 5)),
            hist_wrenches=torch.zeros((B, 10, 6)),
            hist_mask=torch.ones((B, 10), dtype=torch.bool),
            future_actions=torch.zeros((B, 20, 5)),
            future_action_mask=torch.ones((B, 20), dtype=torch.bool),
            extra_inputs_for_audit={"step_mu_true": torch.ones(B)},
        )


def test_task_dimension_validation_and_no_mixed_task_batching() -> None:
    """Verify Prelift (23/5) and Peg-in-Hole (15/6) remain separated and validated."""
    model = HCTTWorldModel()
    with pytest.raises(ContractViolationError):
        # Pass Peg-in-Hole 15-D state into Prelift interface (expects 23-D)
        model(
            task="compliant_prelift",
            hist_states=torch.zeros((2, 10, 15)),
            hist_prev_actions=torch.zeros((2, 10, 5)),
            hist_wrenches=torch.zeros((2, 10, 6)),
            hist_mask=torch.ones((2, 10), dtype=torch.bool),
            future_actions=torch.zeros((2, 20, 5)),
            future_action_mask=torch.ones((2, 20), dtype=torch.bool),
        )

    pre_ds = HCTTTemporalDataset(TRAINING_DIR, task_filter="compliant_prelift")
    peg_ds = HCTTTemporalDataset(TRAINING_DIR, task_filter="peg_in_hole")
    with pytest.raises(ContractViolationError):
        collate_hctt_batch([pre_ds[0], peg_ds[0]])


def test_window_construction_alignment_and_causal_leakage_prevention() -> None:
    """Verify past-action causality, future target alignment, and strict future causal masking."""
    peg_ds = HCTTTemporalDataset(TRAINING_DIR, task_filter="peg_in_hole")
    ep0 = peg_ds.episodes[0]

    # Pick anchor step t=10 inside Peg-in-Hole (T=30):
    # Full 10-step history [1..10], full 20-step future [10..29]
    win_idx = next(i for i, w in enumerate(peg_ds.windows) if w.episode_idx == 0 and w.anchor_step == 10)
    item = peg_ds[win_idx]
    assert item["valid_hist_len"] == 10
    assert item["valid_future_len"] == 20
    assert np.all(item["hist_mask"])
    assert np.all(item["future_mask"])

    # Past-action causality check:
    # Last history slot corresponds to state s_10; its prev_action must be a_9, NOT a_10!
    assert np.allclose(item["hist_states"][-1], ep0.states[10])
    assert np.allclose(item["hist_prev_actions"][-1], ep0.actions[9])
    assert np.allclose(item["future_actions"][0], ep0.actions[10])
    assert np.allclose(item["target_next_states"][0], ep0.next_states[10])
    assert np.allclose(item["target_next_states"][19], ep0.next_states[29])

    # Verify causal masking inside HCTTWorldModel:
    # Changing future_actions at step k=5 MUST NOT alter history representation, sysID, or predictions at k < 5.
    model = HCTTWorldModel()
    model.eval()
    batch = collate_hctt_batch([item])
    inputs_a, _ = split_batch_inputs_and_targets(batch)
    inputs_b = {k: (v.clone() if isinstance(v, torch.Tensor) else v) for k, v in inputs_a.items()}
    inputs_b["future_actions"][:, 5:, :] += 50.0  # large perturbation at future steps >= 5

    with torch.no_grad():
        out_a = model(**inputs_a)
        out_b = model(**inputs_b)

    assert torch.allclose(out_a["latent_history_repr"], out_b["latent_history_repr"], atol=1e-6)
    assert torch.allclose(out_a["pred_friction_mu"], out_b["pred_friction_mu"], atol=1e-6)
    assert torch.allclose(out_a["pred_next_states"][:, :5, :], out_b["pred_next_states"][:, :5, :], atol=1e-6)
    # Perturbed future steps >= 5 MUST differ
    assert not torch.allclose(out_a["pred_next_states"][:, 5:, :], out_b["pred_next_states"][:, 5:, :], atol=1e-5)


def test_strict_unpadded_window_feasibility_matches_episode_lengths() -> None:
    """Verify Prelift (T=20) has 0 unpadded (H_hist=10, H_fut=20) windows while Peg (T=30) has 2/ep."""
    pre_20 = HCTTTemporalDataset(TRAINING_DIR, task_filter="compliant_prelift", window_mode="strict_unpadded", min_future_steps=20)
    peg_20 = HCTTTemporalDataset(TRAINING_DIR, task_filter="peg_in_hole", window_mode="strict_unpadded", min_future_steps=20)
    assert len(pre_20) == 0
    assert len(peg_20) == 500 * 2  # t=9 and t=10 per 30-step episode


def test_audit_target_loader_hmac_and_fail_closed_corruption(tmp_path: Path) -> None:
    """Verify AuditTargetLoader succeeds on valid audit data and fails closed on HMAC/salt/corrupt mismatch."""
    loader = AuditTargetLoader(PROD_ROOT, audit_salt=STAGE3_DETERMINISTIC_AUDIT_SALT)
    assert len(loader.targets_by_sample_id) == 1000
    assert set(loader.mode_histogram.keys()) == {0, 1, 2, 3, 4}

    # Wrong salt must fail closed
    with pytest.raises(ContractViolationError):
        AuditTargetLoader(PROD_ROOT, audit_salt=b"wrong_salt")

    # Corrupted audit file must fail closed
    fake_root = tmp_path / "prod"
    (fake_root / "audit").mkdir(parents=True)
    shutil.copy2(PROD_ROOT / "accepted_manifest.json", fake_root / "accepted_manifest.json")
    shutil.copy2(PROD_ROOT / "audit" / "stage3_3_audit_manifest.json", fake_root / "audit" / "stage3_3_audit_manifest.json")
    with pytest.raises(ContractViolationError):
        AuditTargetLoader(fake_root, audit_salt=STAGE3_DETERMINISTIC_AUDIT_SALT)


def test_episode_split_isolation_and_protected_pools() -> None:
    """Verify 70/10/10/10 episode splits have zero overlap across splits and historical/reserve seeds."""
    manifest = build_episode_split_manifest(PROD_ROOT, split_seed=3402026)
    assert verify_split_manifest_disjointness(manifest) is True
    assert manifest["splits"]["train"]["num_episodes"] == 700
    assert manifest["splits"]["val"]["num_episodes"] == 100
    assert manifest["splits"]["calib"]["num_episodes"] == 100
    assert manifest["splits"]["test"]["num_episodes"] == 100
    assert manifest["reserve_pool_consumed"] == 0


def test_loss_computation_no_grad_distillation_gating_and_checkpoint_roundtrip(tmp_path: Path) -> None:
    """Verify finite loss, distillation gating when teacher reps absent vs present, and checkpoint reload."""
    loader = AuditTargetLoader(PROD_ROOT, audit_salt=STAGE3_DETERMINISTIC_AUDIT_SALT)
    manifest = build_episode_split_manifest(PROD_ROOT, split_seed=3402026)
    test_ds = HCTTTemporalDataset(
        TRAINING_DIR,
        task_filter="compliant_prelift",
        allowed_sample_ids=manifest["splits"]["test"]["sample_ids"],
    )
    batch = test_ds.iter_homogeneous_batches(batch_size=8, task="compliant_prelift", shuffle=False)[0]
    model_inputs, sim_targets = split_batch_inputs_and_targets(batch)
    audit_targets = loader.get_batch_targets(batch["sample_ids"], batch["anchor_steps"], batch["future_mask"])

    runner = HCTTTrainingRunner(seed=3400)
    res_no_distill = runner.verify_forward_and_loss_no_grad(model_inputs, sim_targets, audit_targets)
    assert res_no_distill["forward_ok"] is True
    assert res_no_distill["distillation_active"] is False
    assert res_no_distill["l_distill"] == 0.0
    assert np.isfinite(res_no_distill["loss_total"])

    # When explicit teacher representations are passed, L_distill activates
    loss_fn = HCTTMultiObjectiveLoss()
    with torch.no_grad():
        out = runner.model(**model_inputs)
        audit_with_rep = dict(audit_targets)
        audit_with_rep["teacher_representations"] = torch.ones_like(out["latent_history_repr"])
        res_with_distill = loss_fn(out, sim_targets, audit_with_rep)
    assert res_with_distill["distillation_active"] is True
    assert float(res_with_distill["l_distill"].item()) > 0.0

    # Untrained checkpoint roundtrip preserves weights identically
    ckpt_path = runner.save_untrained_checkpoint(tmp_path / "hctt_init.pt")
    reloaded = HCTTTrainingRunner.load_untrained_checkpoint(ckpt_path)
    with torch.no_grad():
        out_reloaded = reloaded.model(**model_inputs)
    assert torch.allclose(out["pred_next_states"], out_reloaded["pred_next_states"], atol=1e-7)
    assert torch.allclose(out["pred_mode_probs"], out_reloaded["pred_mode_probs"], atol=1e-7)

    # Read-only evaluator smoke check
    evaluator = HCTTReadOnlyEvaluator(loader)
    eval_res = evaluator.evaluate_hctt(runner.model, test_ds, batch_size=16)
    assert eval_res["finite_outputs_verified"] is True
    assert eval_res["trust_calibrated"] is False
    assert eval_res["conformal_coverage"] == "UNCALIBRATED_PRETRAINING"
