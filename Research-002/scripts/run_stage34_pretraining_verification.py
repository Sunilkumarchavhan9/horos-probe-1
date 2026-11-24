"""Stage 3.4 Pre-Training Verification & Read-Only Evaluation Runner.

Executes strictly read-only (zero gradient updates, training_permitted=0, execution_authorized=0):
1. Generates and persists the immutable episode split manifest (stage34_episode_split_manifest.json).
2. Audits full-dataset loading and sliding-window feasibility across both tasks and all 5 horizons.
3. Verifies AuditTargetLoader HMAC provenance and mode distributions across all 1,000 episodes.
4. Executes HCTT-256 architecture parameter count, no-grad forward/loss pass, and checkpoint save/reload check.
5. Evaluates frozen B1/B2/B3 checkpoints and untrained HCTT baseline on the Stage 3.4 test split.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import torch

SCRIPT_DIR = Path(__file__).resolve().parent
R002_ROOT = SCRIPT_DIR.parent
for p in (R002_ROOT, R002_ROOT / "src"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)

from integrated.model_training_runner import load_hybrid_checkpoint
from scripts.run_stage3_production_dataset import STAGE3_DETERMINISTIC_AUDIT_SALT
from world_model_vnext import (
    AuditTargetLoader,
    EXECUTION_AUTHORIZED,
    HCTTReadOnlyEvaluator,
    HCTTTemporalDataset,
    HCTTTrainingRunner,
    TRAINING_PERMITTED,
    build_episode_split_manifest,
    split_batch_inputs_and_targets,
    verify_split_manifest_disjointness,
)


def main() -> None:
    prod_root = R002_ROOT / "experiments" / "stage3_production"
    training_dir = prod_root / "training"

    assert TRAINING_PERMITTED == 0 and EXECUTION_AUTHORIZED == 0

    # 1. Generate and persist immutable episode split manifest
    split_manifest = build_episode_split_manifest(prod_root, split_seed=3402026)
    verify_split_manifest_disjointness(split_manifest)
    split_manifest_path = prod_root / "stage34_episode_split_manifest.json"
    split_manifest_path.write_text(json.dumps(split_manifest, indent=2) + "\n", encoding="utf-8")
    print("Saved split manifest:", split_manifest_path)

    # 2. Load datasets for full feasibility audit and split isolation
    full_masked = HCTTTemporalDataset(training_dir, window_mode="masked_sliding")
    full_unpadded = HCTTTemporalDataset(training_dir, window_mode="strict_unpadded", min_future_steps=1)
    full_unpadded_h20 = HCTTTemporalDataset(training_dir, window_mode="strict_unpadded", min_future_steps=20)
    full_unpadded_h10 = HCTTTemporalDataset(training_dir, window_mode="strict_unpadded", min_future_steps=10)

    feasibility = {
        "masked_sliding": full_masked.feasibility_summary(),
        "strict_unpadded_min_fut_1": full_unpadded.feasibility_summary(),
        "strict_unpadded_min_fut_10": full_unpadded_h10.feasibility_summary(),
        "strict_unpadded_min_fut_20": full_unpadded_h20.feasibility_summary(),
    }
    print("Window feasibility summary:", json.dumps(feasibility, indent=2))

    # 3. Verify AuditTargetLoader across all 1,000 episodes
    target_loader = AuditTargetLoader(prod_root, audit_salt=STAGE3_DETERMINISTIC_AUDIT_SALT)
    print("Verified audit episodes:", len(target_loader.targets_by_sample_id))
    print("Verified mode histogram:", target_loader.mode_histogram)

    # 4. Initialize HCTTTrainingRunner (locked at zero training) & inspect no-grad forward + loss
    runner = HCTTTrainingRunner(seed=3400)
    param_summary = runner.model.parameter_summary()
    print("HCTT-256 parameter summary:", json.dumps(param_summary, indent=2))

    no_grad_checks: Dict[str, Any] = {}
    for task in ("compliant_prelift", "peg_in_hole"):
        train_ds_task = HCTTTemporalDataset(
            training_dir,
            task_filter=task,
            allowed_sample_ids=split_manifest["splits"]["train"]["sample_ids"],
        )
        batch = train_ds_task.iter_homogeneous_batches(batch_size=16, task=task, shuffle=False)[0]
        model_inputs, sim_targets = split_batch_inputs_and_targets(batch)
        audit_targets = target_loader.get_batch_targets(
            batch["sample_ids"], batch["anchor_steps"], batch["future_mask"]
        )
        res = runner.verify_forward_and_loss_no_grad(model_inputs, sim_targets, audit_targets)
        no_grad_checks[task] = res
        print(f"No-grad check [{task}]:", res)

    # Checkpoint roundtrip verification (untrained only)
    with tempfile.TemporaryDirectory() as tmpdir:
        ckpt_path = runner.save_untrained_checkpoint(Path(tmpdir) / "hctt_untrained.pt")
        reloaded = HCTTTrainingRunner.load_untrained_checkpoint(ckpt_path)
        reloaded.assert_training_locked()

    # 5. Read-only evaluation on exact Stage 3.4 test split (100 episodes: 50 prelift, 50 peg)
    evaluator = HCTTReadOnlyEvaluator(target_loader)
    test_sids = split_manifest["splits"]["test"]["sample_ids"]
    test_eval_results: Dict[str, Any] = {
        "training_permitted": TRAINING_PERMITTED,
        "execution_authorized": EXECUTION_AUTHORIZED,
        "training_steps_executed": 0,
        "parameter_updates_executed": 0,
        "parameter_summary": param_summary,
        "window_feasibility": feasibility,
        "no_grad_verification": no_grad_checks,
        "tasks": {},
    }

    for task in ("compliant_prelift", "peg_in_hole"):
        test_ds = HCTTTemporalDataset(training_dir, task_filter=task, allowed_sample_ids=test_sids)
        untrained_hctt_eval = evaluator.evaluate_hctt(runner.model, test_ds, batch_size=32)

        baselines_eval: Dict[str, Any] = {}
        for arch in ("b1_deterministic", "b2_probabilistic", "b3_ensemble"):
            ckpt = R002_ROOT / "checkpoints" / "phase3_4" / task / "M4_acmm" / f"{arch}.pt"
            hybrid, _ = load_hybrid_checkpoint(str(ckpt))
            baselines_eval[arch] = evaluator.evaluate_frozen_baseline_on_dataset(hybrid, test_ds, task=task, batch_size=64)

        test_eval_results["tasks"][task] = {
            "num_test_episodes": len(test_ds.episodes),
            "num_test_windows": len(test_ds),
            "untrained_hctt_smoke": untrained_hctt_eval,
            "frozen_baselines_m4_acmm": baselines_eval,
        }

    eval_out_path = prod_root / "stage34_pretraining_eval_metrics.json"
    eval_out_path.write_text(json.dumps(test_eval_results, indent=2) + "\n", encoding="utf-8")
    print("Saved pre-training evaluation report JSON:", eval_out_path)


if __name__ == "__main__":
    main()
