"""Stage 3.5 Scientific Gate Closure, Prelift T=40 & Final Holdout Generation, and Read-Only Benchmarks.

Strictly read-only with respect to learning:
    TRAINING_PERMITTED = 0
    EXECUTION_AUTHORIZED = 0
Zero gradient steps, zero backward passes, zero optimizer updates, zero modifications to Stage 3.3 archives
or frozen Phase 3.4 checkpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

SCRIPT_DIR = Path(__file__).resolve().parent
R002_ROOT = SCRIPT_DIR.parent
for p in (R002_ROOT, R002_ROOT / "src"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)

from contact_modes.contact_mode_definitions import MODE_NAMES, classify_contact_mode
from environment.peg_in_hole_env import PegInHoleEnv
from integrated.calibration_runner import PHASE3_SEED_POOLS
from integrated.model_training_runner import load_hybrid_checkpoint
from scripts.run_stage3_production_dataset import Stage3ProductionTeacherController
from scripts.stage3_production_adapter import (
    PEG_CONTROL_DT_S,
    ProductionTrainingDataloader,
    Stage3ProductionExperienceFactory,
    assert_tensor_channels_clean,
    build_training_package_and_audit,
    validate_model_visible_package,
    verify_production_physical_interaction,
)
from selection_aware.counterfactual_rollout import extract_env_state_and_wrench
from teaching.factory import EnvironmentSamplingSpec
from teaching.inference import (
    TeacherInferenceEngine,
    TeacherModelDescriptor,
    TeacherModelRegistry,
    TeacherProvider,
)
from teaching.validator import PhysicsValidationEngine
from world_model_vnext import (
    AuditTargetLoader,
    EXECUTION_AUTHORIZED,
    EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG,
    HCTTMultiObjectiveLoss,
    HCTTReadOnlyEvaluator,
    HCTTTemporalDataset,
    HCTTTrainingRunner,
    STAGE35_PROTECTED_EXTRA_RANGES,
    TRAINING_PERMITTED,
    build_stage35_final_split_manifest,
    split_batch_inputs_and_targets,
)


STAGE35_SEED_BASE = 3_500_000

# Two isolated sub-campaigns under experiments/stage35_production/:
# 1. prelift_long (500 episodes, T=40): train/val/calib long-horizon Prelift
# 2. final_holdout (120 episodes: 60 Prelift T=40 + 60 Peg-in-Hole T=30): strictly sealed final_test
STAGE35_CAMPAIGN_ALLOCATIONS = {
    "prelift_long": {
        "base_offset": 0,  # seeds 3,511,xxx .. 3,513,xxx
        "counts": {
            "compliant_prelift": {"nominal": 250, "probe": 150, "nonstationary": 100},
        },
        "lengths": {"compliant_prelift": 40},
    },
    "final_holdout": {
        "base_offset": 20_000,  # seeds 3,531,xxx .. 3,545,xxx
        "counts": {
            "compliant_prelift": {"nominal": 30, "probe": 18, "nonstationary": 12},
            "peg_in_hole": {"nominal": 30, "impact": 18, "wedging": 12},
        },
        "lengths": {"compliant_prelift": 40, "peg_in_hole": 30},
    },
}

TASK_CODES = {"compliant_prelift": 1, "peg_in_hole": 2}
STRATUM_CODES = {"nominal": 1, "probe": 2, "nonstationary": 3, "impact": 4, "wedging": 5}


def resolve_stage35_runtime_salt() -> Tuple[bytes, str]:
    """Obtains HMAC salt from env var or fresh OS CSPRNG — NEVER from a committed literal."""
    env_hex = os.environ.get("HOROS_STAGE35_AUDIT_SALT_HEX", "").strip()
    if env_hex:
        salt = bytes.fromhex(env_hex)
        if len(salt) < 16:
            raise ValueError("HOROS_STAGE35_AUDIT_SALT_HEX must represent at least 16 bytes.")
        return salt, "env_HOROS_STAGE35_AUDIT_SALT_HEX"
    return os.urandom(32), "runtime_os_urandom_32bytes"


def stage35_seed(campaign_offset: int, task: str, stratum: str, index: int) -> int:
    return (
        STAGE35_SEED_BASE
        + int(campaign_offset)
        + TASK_CODES[task] * 10_000
        + STRATUM_CODES[stratum] * 1_000
        + int(index)
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_stage35_subdataset(
    name: str,
    out_root: Path,
    factory: Stage3ProductionExperienceFactory,
    validator: PhysicsValidationEngine,
    teacher_id: str,
    audit_salt: bytes,
    salt_source: str,
) -> Dict[str, Any]:
    """Generates one isolated Stage 3.5 subdataset with SHA-256 file manifests and firewall validation."""
    spec = STAGE35_CAMPAIGN_ALLOCATIONS[name]
    offset = spec["base_offset"]
    counts = spec["counts"]
    lengths = spec["lengths"]

    training_dir = out_root / "training"
    audit_dir = out_root / "audit"
    rejected_dir = out_root / "rejected"
    training_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    accepted_manifest: List[Dict[str, Any]] = []
    rejected_manifest: List[Dict[str, Any]] = []
    mode_counts: Counter[int] = Counter()
    file_checksums: Dict[str, str] = {}

    for task, strata_map in counts.items():
        ep_len = lengths[task]
        for stratum, n_eps in strata_map.items():
            for idx in range(n_eps):
                seed = stage35_seed(offset, task, stratum, idx)
                # Verify zero overlap with ALL protected historical + Stage 3.3 + reserve + P5-T4R4 ranges
                for pool_name, pool_seeds in STAGE35_PROTECTED_EXTRA_RANGES.items():
                    if seed in pool_seeds:
                        raise RuntimeError(f"Seed {seed} collides with protected pool {pool_name}")

                params = factory.sample_stratified_scenario(task, stratum, idx)
                params["seed"] = seed
                params["slot_alias"] = f"prod35_{name}_{task}_{stratum}_{idx:03d}"

                record = factory.generate_episode(
                    task=task,
                    teacher_id=teacher_id,
                    seed=seed,
                    episode_length=ep_len,
                    parameter_overrides=params,
                    slot_alias=params["slot_alias"],
                )
                phys = validator.validate_trajectory(
                    task_id=task,
                    states=record.states,
                    actions=record.actions,
                    wrenches=record.wrenches,
                    env_metadata=record.env_metadata,
                )
                inter_ok, inter_metrics = verify_production_physical_interaction(task, record)
                is_accepted = bool(phys.is_valid and inter_ok)

                package, audit = build_training_package_and_audit(record, audit_salt)
                # Ensure record in-memory HMAC matches exactly
                recomputed = hmac.new(
                    audit_salt, str(audit["canonical_episode_id"]).encode("utf-8"), hashlib.sha256
                ).hexdigest()
                if recomputed != audit["sample_id"]:
                    raise RuntimeError("HMAC verification failed during generation")

                modes_list = [int(m) for m in record.modes]
                mode_counts.update(modes_list)

                sid = audit["sample_id"]
                cid = audit["canonical_episode_id"]

                if is_accepted:
                    npz_path = training_dir / f"{sid}.npz"
                    np.savez_compressed(
                        npz_path,
                        states=package["states"],
                        actions=package["actions"],
                        next_states=package["next_states"],
                        wrenches=package["wrenches"],
                        next_wrenches=package["next_wrenches"],
                        sample_id=package["sample_id"],
                        task=package["task"],
                    )
                    priv_path = audit_dir / f"{cid}.privileged.json"
                    priv_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

                    npz_sha = _sha256_file(npz_path)
                    priv_sha = _sha256_file(priv_path)
                    file_checksums[f"training/{sid}.npz"] = npz_sha
                    file_checksums[f"audit/{cid}.privileged.json"] = priv_sha

                    accepted_manifest.append({
                        "sample_id": sid,
                        "canonical_episode_id": cid,
                        "slot_alias": params["slot_alias"],
                        "seed": int(seed),
                        "task": task,
                        "stratum": stratum,
                        "index": int(idx),
                        "episode_length": int(ep_len),
                        "provenance_hash": record.provenance_hash,
                        "npz_sha256": npz_sha,
                        "privileged_json_sha256": priv_sha,
                        "interaction_metrics": inter_metrics,
                    })
                else:
                    rej = {
                        "canonical_episode_id": cid,
                        "seed": int(seed),
                        "task": task,
                        "stratum": stratum,
                        "index": int(idx),
                        "physics_errors": phys.violation_details,
                        "interaction_metrics": inter_metrics,
                    }
                    rejected_manifest.append(rej)
                    (rejected_dir / f"{cid}.rejected.json").write_text(
                        json.dumps(rej, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                    )

    (out_root / "accepted_manifest.json").write_text(json.dumps(accepted_manifest, indent=2) + "\n", encoding="utf-8")
    (out_root / "rejected_manifest.json").write_text(json.dumps(rejected_manifest, indent=2) + "\n", encoding="utf-8")

    audit_manifest = {
        "manifest_version": "1.0.0",
        "protocol": f"HOROS-P1-R002-STAGE3.5-{name.upper()}",
        "audit_salt_sha256": hashlib.sha256(audit_salt).hexdigest(),
        "salt_source": salt_source,
        "hmac_verified_at_generation": True,
        "total_generated": len(accepted_manifest) + len(rejected_manifest),
        "total_accepted": len(accepted_manifest),
        "total_rejected": len(rejected_manifest),
        "task_episode_lengths": lengths,
        "mode_distribution": {MODE_NAMES.get(m, f"MODE_{m}"): c for m, c in sorted(mode_counts.items())},
        "mode_counts_by_id": dict(mode_counts),
    }
    (audit_dir / "stage35_audit_manifest.json").write_text(
        json.dumps(audit_manifest, indent=2) + "\n", encoding="utf-8"
    )

    # Verify clean ingestion & zero leakage
    loader = ProductionTrainingDataloader(training_dir)
    for sample in loader.samples:
        validate_model_visible_package(sample)
        assert_tensor_channels_clean(str(sample["task"]), sample["states"], sample["wrenches"], {})
        assert_tensor_channels_clean(str(sample["task"]), sample["next_states"], sample["next_wrenches"], {})

    # Also validate AuditTargetLoader with runtime salt
    target_loader = AuditTargetLoader(out_root, audit_salt=audit_salt)
    if len(target_loader.targets_by_sample_id) != len(accepted_manifest):
        raise RuntimeError("AuditTargetLoader count mismatch")

    return {
        "subdataset": name,
        "total_generated": audit_manifest["total_generated"],
        "total_accepted": audit_manifest["total_accepted"],
        "total_rejected": audit_manifest["total_rejected"],
        "task_episode_lengths": lengths,
        "audit_salt_sha256": audit_manifest["audit_salt_sha256"],
        "salt_source": salt_source,
        "mode_counts_by_id": dict(mode_counts),
        "file_checksums": file_checksums,
    }


def evaluate_frozen_peg_native_dt02(r002_root: Path) -> Dict[str, Any]:
    """Evaluates frozen Phase 3.4 Peg-in-Hole B1/B2/B3 at NATIVE dt=0.02s for matched physical-time comparisons."""
    seeds = list(PHASE3_SEED_POOLS["offline_test_peg_in_hole"])  # 40 held-out seeds 3080..3119
    horizon_steps = 20  # 20 steps * 0.02s = 0.40s
    episodes = []

    for seed in seeds:
        env = PegInHoleEnv(dt=0.02, seed=seed)
        obs = env.reset()
        curr_s, _ = extract_env_state_and_wrench(obs, task="peg_in_hole")
        states = np.zeros((horizon_steps, len(curr_s)), dtype=np.float32)
        next_states = np.zeros((horizon_steps, len(curr_s)), dtype=np.float32)
        actions = np.zeros((horizon_steps, 6), dtype=np.float32)
        modes = np.zeros((horizon_steps,), dtype=np.int64)
        curr_obs = obs
        rng = np.random.RandomState(seed + 999)
        for t in range(horizon_steps):
            a_t = np.array([rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1), -0.8, 0.0, 0.0, 0.0], dtype=np.float32)
            states[t] = curr_s
            actions[t] = a_t
            next_obs, _, _, _ = env.step(a_t)
            next_s, _ = extract_env_state_and_wrench(next_obs, task="peg_in_hole")
            next_states[t] = next_s
            modes[t] = int(classify_contact_mode(next_obs, prev_state=curr_obs, dt=0.02, task="peg_in_hole"))
            curr_obs = next_obs
            curr_s = next_s
        episodes.append({"states": states, "actions": actions, "next_states": next_states, "modes": modes})

    # Physical times matching Stage 3/4: k_02=1 (0.02s), k_02=3 (0.06s), k_02=5 (0.10s), k_02=10 (0.20s)
    k_steps = [1, 3, 5, 10]
    results: Dict[str, Any] = {
        "dt_s": 0.02,
        "seed_pool": "PHASE3_SEED_POOLS['offline_test_peg_in_hole'] (3080..3119)",
        "num_episodes": len(episodes),
        "architectures": {},
    }

    for arch in ("b1_deterministic", "b2_probabilistic", "b3_ensemble"):
        ckpt = r002_root / "checkpoints" / "phase3_4" / "peg_in_hole" / "M4_acmm" / f"{arch}.pt"
        hybrid, _ = load_hybrid_checkpoint(str(ckpt))
        hybrid.eval()
        errs: Dict[int, List[float]] = {k: [] for k in k_steps}
        with torch.no_grad():
            for ep in episodes:
                # Rollout from every anchor t where t + 10 <= 20 (t in 0..10)
                for t in range(0, horizon_steps - 10 + 1):
                    curr = torch.tensor(ep["states"][t], dtype=torch.float32).unsqueeze(0)
                    for k in range(1, 11):
                        a_k = torch.tensor(ep["actions"][t + k - 1], dtype=torch.float32).unsqueeze(0)
                        mean_k, _, _ = hybrid.predict_with_uncertainty(curr, a_k)
                        if k in errs:
                            true_k = ep["next_states"][t + k - 1]
                            errs[k].append(float(np.mean((mean_k.squeeze(0).numpy() - true_k) ** 2)))
                        curr = mean_k
        results["architectures"][arch] = {
            f"T_{round(k * 0.02, 2):.2f}s_k{k}": {
                "physical_time_s": round(k * 0.02, 4),
                "step_horizon_dt02": k,
                "equivalent_dt01_steps": k * 2,
                "n_rollouts": len(errs[k]),
                "state_mse": round(float(np.mean(errs[k])), 6),
            }
            for k in k_steps
        }
    return results


def main() -> None:
    assert TRAINING_PERMITTED == 0 and EXECUTION_AUTHORIZED == 0
    s33_root = R002_ROOT / "experiments" / "stage3_production"
    s35_root = R002_ROOT / "experiments" / "stage35_production"
    s35_root.mkdir(parents=True, exist_ok=True)

    # Record baseline Stage 3.3 file checksums BEFORE running anything to prove non-modification
    s33_before_sha = {
        "accepted_manifest": _sha256_file(s33_root / "accepted_manifest.json"),
        "stage34_split_manifest": _sha256_file(s33_root / "stage34_episode_split_manifest.json"),
    }

    reg = TeacherModelRegistry()
    desc = TeacherModelDescriptor(
        model_id="stage3_production_teacher_v1",
        display_name="Stage 3 Production Teacher Controller",
        version="1.0.0",
        provider=TeacherProvider.MOCK_DETERMINISTIC,
        supported_modalities=["state_vector"],
        capabilities=["trajectory_proposal"],
        license="internal_horos_research",
        has_representations=False,
        deterministic_seed_supported=True,
    )
    reg.register(desc)
    engine = TeacherInferenceEngine(reg)
    engine.register_adapter(desc.model_id, Stage3ProductionTeacherController(desc))
    factory = Stage3ProductionExperienceFactory(
        engine, sampling_spec=EnvironmentSamplingSpec(default_episode_length=40, dt_s=PEG_CONTROL_DT_S)
    )
    validator = PhysicsValidationEngine.for_tasks()

    audit_salt, salt_source = resolve_stage35_runtime_salt()
    print(f"=== Running Stage 3.5 Data Generation (salt source: {salt_source}) ===")

    pre_long_summary = generate_stage35_subdataset(
        "prelift_long", s35_root / "prelift_long", factory, validator, desc.model_id, audit_salt, salt_source
    )
    holdout_summary = generate_stage35_subdataset(
        "final_holdout", s35_root / "final_holdout", factory, validator, desc.model_id, audit_salt, salt_source
    )

    dataset_manifest = {
        "manifest_version": "stage35_dataset_manifest_v1.0",
        "protocol": "HOROS-P1-R002-STAGE3.5-DATASET-MANIFEST",
        "date_generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage33_archives_preserved_unmodified": True,
        "stage33_reference_hashes": s33_before_sha,
        "subdatasets": {
            "prelift_long": pre_long_summary,
            "final_holdout": holdout_summary,
        },
    }
    (s35_root / "stage35_dataset_manifest.json").write_text(
        json.dumps(dataset_manifest, indent=2) + "\n", encoding="utf-8"
    )

    # Build leak-free Final Split Manifest (quarantining Stage 3.4 test split & keeping final_test sealed)
    final_split = build_stage35_final_split_manifest(
        stage33_root=s33_root,
        stage34_split_manifest_path=s33_root / "stage34_episode_split_manifest.json",
        stage35_prelift_long_root=s35_root / "prelift_long",
        stage35_final_holdout_root=s35_root / "final_holdout",
    )
    (s35_root / "stage35_final_split_manifest.json").write_text(
        json.dumps(final_split, indent=2) + "\n", encoding="utf-8"
    )

    # Recompute exact temporal window counts across Stage 3.3 (Prelift T=20, Peg T=30) & Stage 3.5 (Prelift T=40)
    ds_s33_pre = HCTTTemporalDataset(s33_root / "training", task_filter="compliant_prelift")
    ds_s33_peg = HCTTTemporalDataset(s33_root / "training", task_filter="peg_in_hole")
    ds_s35_pre = HCTTTemporalDataset(s35_root / "prelift_long" / "training", task_filter="compliant_prelift")

    # Also compute strict & masked splits for train/val/calib (NEVER touching final_test!)
    ds_train_strict = HCTTTemporalDataset(
        [s35_root / "prelift_long" / "training", s33_root / "training"],
        allowed_sample_ids=final_split["splits"]["train"]["sample_ids"],
        window_mode="strict_unpadded",
    )
    ds_val_masked = HCTTTemporalDataset(
        [s35_root / "prelift_long" / "training", s33_root / "training"],
        allowed_sample_ids=final_split["splits"]["val"]["sample_ids"],
        window_mode="masked_sliding",
    )

    # Verify strict Path 1 vs masked Path 2 read-only evaluation + matched-timestep benchmarks on VAL ONLY
    target_loader_combined = AuditTargetLoader([s33_root, s35_root / "prelift_long"])
    evaluator = HCTTReadOnlyEvaluator(target_loader_combined)
    runner_locked = HCTTTrainingRunner(seed=3500)
    runner_exp3term = HCTTTrainingRunner(config=EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG, seed=3500)

    # Verify experimental 3-term objective vs locked 4-term under torch.no_grad() on VAL batch
    val_batch_pre = ds_val_masked.iter_homogeneous_batches(batch_size=16, task="compliant_prelift", shuffle=False)[0]
    m_in, s_tgt = split_batch_inputs_and_targets(val_batch_pre)
    a_tgt = target_loader_combined.get_batch_targets(val_batch_pre["sample_ids"], val_batch_pre["anchor_steps"], val_batch_pre["future_mask"])
    res_4term = runner_locked.verify_forward_and_loss_no_grad(m_in, s_tgt, a_tgt)
    res_3term = runner_exp3term.verify_forward_and_loss_no_grad(m_in, s_tgt, a_tgt)

    # Path 1 vs Path 2 Untrained HCTT Smoke Check on VAL partition (Prelift T=40 & Peg T=30)
    val_strict_vs_masked = {
        "compliant_prelift_T40_val": evaluator.evaluate_strict_vs_masked(runner_locked.model, ds_val_masked, task="compliant_prelift", batch_size=32),
        "peg_in_hole_T30_val": evaluator.evaluate_strict_vs_masked(runner_locked.model, ds_val_masked, task="peg_in_hole", batch_size=32),
    }

    # Matched-timestep benchmarks on VAL / native baselines (final_holdout remains UNTOUCHED)
    pers_cvel_prelift = evaluator.evaluate_persistence_and_const_vel_baselines(ds_val_masked, task="compliant_prelift", strict_only=True)
    pers_cvel_peg = evaluator.evaluate_persistence_and_const_vel_baselines(ds_val_masked, task="peg_in_hole", strict_only=True)

    prelift_frozen_matched = {}
    for arch in ("b1_deterministic", "b2_probabilistic", "b3_ensemble"):
        ckpt = R002_ROOT / "checkpoints" / "phase3_4" / "compliant_prelift" / "M4_acmm" / f"{arch}.pt"
        hybrid, _ = load_hybrid_checkpoint(str(ckpt))
        # Use strict val dataset for Prelift T=40 at dt=0.01s (exact match!)
        ds_val_pre_strict = HCTTTemporalDataset(
            s35_root / "prelift_long" / "training",
            task_filter="compliant_prelift",
            allowed_sample_ids=final_split["splits"]["val"]["sample_ids"],
            window_mode="strict_unpadded",
            min_future_steps=1,
        )
        prelift_frozen_matched[arch] = evaluator.evaluate_frozen_baseline_on_dataset(
            hybrid, ds_val_pre_strict, task="compliant_prelift", batch_size=64
        )

    peg_frozen_native_dt02 = evaluate_frozen_peg_native_dt02(R002_ROOT)

    # Assert Stage 3.3 files remained 100% unmodified
    assert _sha256_file(s33_root / "accepted_manifest.json") == s33_before_sha["accepted_manifest"]
    assert _sha256_file(s33_root / "stage34_episode_split_manifest.json") == s33_before_sha["stage34_split_manifest"]

    verification_metrics = {
        "training_permitted": TRAINING_PERMITTED,
        "execution_authorized": EXECUTION_AUTHORIZED,
        "gradient_steps": 0,
        "optimizer_updates": 0,
        "student_model_training": "NOT_EXECUTED",
        "final_holdout_evaluated": False,
        "recomputed_window_counts": {
            "stage33_prelift_T20_500ep": ds_s33_pre.feasibility_summary()["compliant_prelift"],
            "stage35_prelift_T40_500ep": ds_s35_pre.feasibility_summary()["compliant_prelift"],
            "stage33_peg_in_hole_T30_500ep": ds_s33_peg.feasibility_summary()["peg_in_hole"],
            "stage35_train_strict_minfut1": ds_train_strict.feasibility_summary(),
        },
        "objective_verification_val_no_grad": {
            "locked_4term": res_4term,
            "experimental_3term": res_3term,
        },
        "val_strict_vs_masked_untrained_hctt": val_strict_vs_masked,
        "matched_timestep_benchmarks": {
            "compliant_prelift_dt01_strict_val": {
                "dt_s": 0.01,
                "frozen_baselines_native_dt01": prelift_frozen_matched,
                "reference_dt01": pers_cvel_prelift,
            },
            "peg_in_hole_dt01_reference_val": pers_cvel_peg,
            "peg_in_hole_frozen_baselines_native_dt02": peg_frozen_native_dt02,
        },
    }
    (s35_root / "stage35_verification_metrics.json").write_text(
        json.dumps(verification_metrics, indent=2) + "\n", encoding="utf-8"
    )
    print("Stage 3.5 verification pipeline complete. Metrics saved to stage35_verification_metrics.json")


if __name__ == "__main__":
    main()
