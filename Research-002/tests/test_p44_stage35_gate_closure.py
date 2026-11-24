"""HOROS PROBE-1 — Research-002: Stage 3.5 Scientific Gate Closure & HCTT-256 Readiness Tests.

Covers all 12 verification requirements for Stage 3.5:
1. Strict 10-step history windowing (valid_hist_len == 10, zero padding on Path 1)
2. Full future-horizon availability per horizon H in {1, 3, 5, 10, 20}
3. Masked vs strict dataset separation (Prelift T=20 -> 0 strict H=20; Prelift T=40 -> 12/ep strict H=20)
4. Prelift T=40 deterministic physics reproducibility (dt=0.01s, episode_length=40)
5. stage35_final_split_manifest.json episode-level isolation across all 6 partitions & sealed final_test
6. Protected-seed exclusion (Stage 3.3, pilot, 300 reserve seeds, Phase 3, 9200..9359, P5-T4R4)
7. Timestep alignment inspection & EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG gating (L_distill == 0.0)
8. Privileged-target isolation (model.forward rejects privileged keys; AuditTargetLoader separation)
9. Live HMAC provenance & runtime CSPRNG salt verification (zero source-code salt literals)
10. Frozen B1-B4 checkpoint immutability (37 files intact, SHA-256 report lock, load check)
11. Historical Stage 3.3 archive immutability (1,000 NPZs + SHA-256 manifest match)
12. Training authorization enforcement (TRAINING_PERMITTED=0, EXECUTION_AUTHORIZED=0, zero updates)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Set

import numpy as np
import pytest
import torch

TESTS_DIR = Path(__file__).resolve().parent
R002_ROOT = TESTS_DIR.parent
for p in (R002_ROOT, R002_ROOT / "src"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)

from environment.peg_in_hole_env import PegInHoleEnv  # noqa: E402
from integrated.model_training_runner import load_hybrid_checkpoint  # noqa: E402
from scripts.run_stage35_pipeline import (  # noqa: E402
    STAGE35_CAMPAIGN_ALLOCATIONS,
    resolve_stage35_runtime_salt,
    stage35_seed,
)
from scripts.run_stage3_production_dataset import (  # noqa: E402
    STAGE3_DETERMINISTIC_AUDIT_SALT,
    Stage3ProductionTeacherController,
)
from scripts.stage3_production_adapter import (  # noqa: E402
    PEG_CONTROL_DT_S,
    Stage3ProductionExperienceFactory,
    verify_deterministic_reproducibility,
)
from teaching.factory import EnvironmentSamplingSpec  # noqa: E402
from teaching.inference import (  # noqa: E402
    TeacherInferenceEngine,
    TeacherModelDescriptor,
    TeacherModelRegistry,
    TeacherProvider,
)
from world_model_vnext import (  # noqa: E402
    AuditTargetLoader,
    ContractViolationError,
    EXECUTION_AUTHORIZED,
    EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG,
    HCTTMultiObjectiveLoss,
    HCTTReadOnlyEvaluator,
    HCTTTemporalDataset,
    HCTTTrainingRunner,
    LOCKED_HCTT_CONFIG,
    STAGE35_PROTECTED_EXTRA_RANGES,
    TRAINING_PERMITTED,
    TrainingAuthorizationError,
    assert_no_privileged_keys_in_inputs,
    split_batch_inputs_and_targets,
    verify_split_manifest_disjointness,
)

S33_ROOT = R002_ROOT / "experiments" / "stage3_production"
S35_ROOT = R002_ROOT / "experiments" / "stage35_production"
S35_PRELIFT_LONG = S35_ROOT / "prelift_long"
S35_FINAL_HOLDOUT = S35_ROOT / "final_holdout"
PHASE34_CKPT_DIR = R002_ROOT / "checkpoints" / "phase3_4"
S33_SEED_REGISTRY = R002_ROOT / "experiments" / "stage3_production_readiness" / "stage3_3_seed_registry.json"


# ---------------------------------------------------------------------------
# 1, 2, 3: Strict 10-step history, full horizons, masked vs strict separation
# ---------------------------------------------------------------------------

def test_strict_10_step_history_and_horizon_availability() -> None:
    """Verify Path 1 enforces valid_hist_len == 10 and full future steps for every horizon H."""
    pre_strict = HCTTTemporalDataset(
        S35_PRELIFT_LONG / "training",
        task_filter="compliant_prelift",
        window_mode="strict_unpadded",
        min_future_steps=1,
    )
    assert len(pre_strict) == 500 * 31  # t in [9..39] for T=40 -> 31 strict anchors/ep

    for w in pre_strict.windows[:200]:
        assert w.valid_hist_len == 10
        assert w.anchor_step >= 9

    for H, expected_per_ep in [(1, 31), (3, 29), (5, 27), (10, 22), (20, 12)]:
        batches = pre_strict.iter_strict_batches_for_horizon(horizon=H, batch_size=256, task="compliant_prelift")
        total_wins = sum(len(b["sample_ids"]) for b in batches)
        assert total_wins == 500 * expected_per_ep
        first_batch = batches[0]
        assert bool(first_batch["hist_mask"].all()) is True
        assert bool(first_batch["future_mask"][:, :H].all()) is True

    # Requesting horizon outside [1, 20] fails closed cleanly
    with pytest.raises((ValueError, ContractViolationError)):
        pre_strict.iter_strict_batches_for_horizon(horizon=25, batch_size=32)


def test_masked_vs_strict_separation_and_episode_length_feasibility() -> None:
    """Verify Prelift T=20 yields 0 strict H=20 windows whereas Prelift T=40 yields 12/ep (6,000)."""
    s33_pre_strict20 = HCTTTemporalDataset(
        S33_ROOT / "training",
        task_filter="compliant_prelift",
        window_mode="strict_unpadded",
        min_future_steps=20,
    )
    s35_pre_strict20 = HCTTTemporalDataset(
        S35_PRELIFT_LONG / "training",
        task_filter="compliant_prelift",
        window_mode="strict_unpadded",
        min_future_steps=20,
    )
    assert len(s33_pre_strict20) == 0
    assert s33_pre_strict20.iter_strict_batches_for_horizon(horizon=20, batch_size=32) == []
    assert len(s35_pre_strict20) == 500 * 12

    s35_masked = HCTTTemporalDataset(
        S35_PRELIFT_LONG / "training",
        task_filter="compliant_prelift",
        window_mode="masked_sliding",
    )
    # Masked sliding includes partial history [1..9] plus full-history 10
    hist_lens = {w.valid_hist_len for w in s35_masked.windows}
    assert hist_lens == set(range(1, 11))
    assert len(s35_masked) == 500 * 40
    # Unfiltered Path 2 batch contains padded left history; Path 1 strict filter excludes them completely
    masked_first = s35_masked.iter_homogeneous_batches(batch_size=32, shuffle=False)[0]
    assert bool(masked_first["hist_mask"].all()) is False
    strict_from_masked = s35_masked.iter_strict_batches_for_horizon(horizon=20, batch_size=128)
    assert sum(len(b["sample_ids"]) for b in strict_from_masked) == 500 * 12
    assert bool(strict_from_masked[0]["hist_mask"].all()) is True
    assert bool(strict_from_masked[0]["future_mask"][:, :20].all()) is True
    with pytest.raises((ValueError, ContractViolationError)):
        HCTTTemporalDataset(S35_PRELIFT_LONG / "training", window_mode="invalid_mode")


# ---------------------------------------------------------------------------
# 4: Prelift T=40 deterministic physics reproducibility (dt=0.01s, T=40)
# ---------------------------------------------------------------------------

def test_prelift_t40_deterministic_reproducibility_all_strata() -> None:
    """Verify bitwise deterministic replay for Prelift T=40 at dt=0.01s across all 3 strata."""
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
    assert factory.sampling_spec.dt_s == 0.01

    for stratum in ("nominal", "probe", "nonstationary"):
        ok, h1, h2 = verify_deterministic_reproducibility(
            factory=factory,
            task="compliant_prelift",
            stratum=stratum,
            index=0,
            teacher_id=desc.model_id,
            episode_length=40,
        )
        assert ok is True
        assert h1 == h2 and len(h1) == 64


# ---------------------------------------------------------------------------
# 5 & 6: Final split isolation & protected-seed exclusion
# ---------------------------------------------------------------------------

def test_final_split_manifest_isolation_and_quarantine() -> None:
    """Verify episode-level zero overlap across all 6 partitions and quarantine of Stage 3.4 test."""
    manifest_path = S35_ROOT / "stage35_final_split_manifest.json"
    s34_path = S33_ROOT / "stage34_episode_split_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    s34 = json.loads(s34_path.read_text(encoding="utf-8"))

    assert verify_split_manifest_disjointness(manifest) is True
    assert manifest["cross_partition_overlap_verified_zero"] is True
    assert manifest["final_test_status"] == "SEALED_UNTIL_AUTHORIZED_STAGE4_EVAL"
    assert manifest["reserve_pool_consumed"] == 0
    assert manifest["p5_t4r4_heldout_overlap_count"] == 0

    splits = manifest["splits"]
    assert splits["train"]["num_episodes"] == 698
    assert splits["val"]["num_episodes"] == 126
    assert splits["calib"]["num_episodes"] == 126
    assert splits["final_test"]["num_episodes"] == 120
    assert splits["exploratory_baseline_quarantined"]["num_episodes"] == 100
    assert splits["legacy_short_horizon_preserved"]["num_episodes"] == 450
    assert manifest["total_episodes_accounted"] == 1620

    # Pairwise zero overlap for both sample_ids and seeds across all 6 partitions
    id_sets: Dict[str, Set[str]] = {k: set(v["sample_ids"]) for k, v in splits.items()}
    seed_sets: Dict[str, Set[int]] = {k: set(int(s) for s in v["seeds"]) for k, v in splits.items()}
    names = list(splits.keys())
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert id_sets[a].isdisjoint(id_sets[b]), f"sample_id overlap between {a} and {b}"
            assert seed_sets[a].isdisjoint(seed_sets[b]), f"seed overlap between {a} and {b}"

    # Verify quarantined Stage 3.4 test set matches identically and is excluded from val/calib/final_test
    s34_test_ids = set(s34["splits"]["test"]["sample_ids"])
    assert id_sets["exploratory_baseline_quarantined"] == s34_test_ids
    for part in ("train", "val", "calib", "final_test", "legacy_short_horizon_preserved"):
        assert id_sets[part].isdisjoint(s34_test_ids)


def test_stage35_protected_seed_exclusion() -> None:
    """Verify all newly generated Stage 3.5 seeds exclude Stage 3.3, pilot, reserve, Phase 3, and P5-T4R4."""
    s35_seeds: Set[int] = set()
    for name, spec in STAGE35_CAMPAIGN_ALLOCATIONS.items():
        offset = int(spec["base_offset"])
        for task, strata_map in spec["counts"].items():
            for stratum, count in strata_map.items():
                for idx in range(int(count)):
                    s35_seeds.add(stage35_seed(offset, task, stratum, idx))
    assert len(s35_seeds) == 620  # 500 prelift_long + 120 final_holdout

    for pool_name, pool_seeds in STAGE35_PROTECTED_EXTRA_RANGES.items():
        assert s35_seeds.isdisjoint(pool_seeds), f"Stage 3.5 seeds collide with {pool_name}"

    # Double-check explicit 300 reserve seeds from registry + P5-T4R4 held-out 4950001..4950600
    registry = json.loads(S33_SEED_REGISTRY.read_text(encoding="utf-8"))
    reserve_seeds = {int(entry["seed"]) for entry in registry["reserve_seed_pool"]["reserve_seeds"]}
    assert len(reserve_seeds) == 300
    assert s35_seeds.isdisjoint(reserve_seeds)
    p5_t4r4_heldout = set(range(4_950_001, 4_950_601))
    assert s35_seeds.isdisjoint(p5_t4r4_heldout)


# ---------------------------------------------------------------------------
# 7: Timestep alignment & experimental 3-term objective gating
# ---------------------------------------------------------------------------

def test_timestep_contract_and_experimental_3term_objective_gating() -> None:
    """Verify env dt mismatch documentation & experimental 3-term L_distill == 0.0 unactivated gate."""
    peg_env = PegInHoleEnv(seed=3501)
    assert peg_env.dt == 0.02
    assert PEG_CONTROL_DT_S == 0.01

    assert LOCKED_HCTT_CONFIG.weight_distill == 0.10
    assert EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG.weight_distill == 0.0
    assert EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG.objective_profile == "experimental_teacher_free_3term_no_distill"
    assert EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG.objective_authorized_for_training is False

    # Verify experimental 3-term keeps distillation weight zero even if teacher reps are passed
    loss_exp = HCTTMultiObjectiveLoss(config=EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG)
    runner = HCTTTrainingRunner(config=EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG, seed=3500)
    ds = HCTTTemporalDataset(S35_PRELIFT_LONG / "training", task_filter="compliant_prelift", window_mode="strict_unpadded")
    loader = AuditTargetLoader(S35_PRELIFT_LONG)
    batch = ds.iter_homogeneous_batches(batch_size=4, task="compliant_prelift", shuffle=False)[0]
    m_in, s_tgt = split_batch_inputs_and_targets(batch)
    a_tgt = loader.get_batch_targets(batch["sample_ids"], batch["anchor_steps"], batch["future_mask"])

    with torch.no_grad():
        out = runner.model(**m_in)
        res_clean = loss_exp(out, s_tgt, a_tgt)
        assert res_clean["distillation_active"] is False
        assert float(res_clean["l_distill"].item()) == 0.0
        assert res_clean["weight_distill"] == 0.0
        assert res_clean["objective_authorized_for_training"] is False

        a_with_rep = dict(a_tgt)
        a_with_rep["teacher_representations"] = torch.ones_like(out["latent_history_repr"])
        res_with_rep = loss_exp(out, s_tgt, a_with_rep)
        expected_3term = (
            1.0 * res_with_rep["l_sim_dynamics"]
            + 0.5 * res_with_rep["l_mode_focal"]
            + 0.25 * res_with_rep["l_sys_id"]
        )
        assert torch.allclose(res_with_rep["loss_total"], expected_3term, atol=1e-6)

    # Verify Persistence & Constant-Velocity matched dt=0.01s reference evaluator works cleanly
    evaluator = HCTTReadOnlyEvaluator(loader)
    ref = evaluator.evaluate_persistence_and_const_vel_baselines(ds, task="compliant_prelift", strict_only=True)
    assert ref["dt_s"] == 0.01
    assert ref["strict_windows_only"] is True
    assert set(ref["persistence"].keys()) == {"H_1", "H_3", "H_5", "H_10", "H_20"}


# ---------------------------------------------------------------------------
# 8 & 9: Privileged isolation & live CSPRNG HMAC provenance
# ---------------------------------------------------------------------------

def test_privileged_isolation_and_live_csprng_hmac_provenance(tmp_path: Path) -> None:
    """Verify zero hardcoded salt literal in Stage 3.5 script and live CSPRNG HMAC verification."""
    script_src = (R002_ROOT / "scripts" / "run_stage35_pipeline.py").read_text(encoding="utf-8")
    # Check Stage 3.5 script contains no hardcoded 64-hex audit salt literal or Stage 3.3 deterministic salt constant
    assert "STAGE3_DETERMINISTIC_AUDIT_SALT" not in script_src
    assert not re.search(r"['\"][0-9a-f]{64}['\"]", script_src.lower())

    salt1, src1 = resolve_stage35_runtime_salt()
    salt2, src2 = resolve_stage35_runtime_salt()
    if "HOROS_STAGE35_AUDIT_SALT_HEX" not in os.environ:
        assert src1 == "runtime_os_urandom_32bytes"
        assert src2 == "runtime_os_urandom_32bytes"
        assert len(salt1) == 32 and len(salt2) == 32
        assert salt1 != salt2

    # Verify live os.urandom(32) HMAC roundtrip on an isolated tmp production root
    live_salt = os.urandom(32)
    live_root = tmp_path / "live_s35"
    (live_root / "audit").mkdir(parents=True)
    sample_audit_files = sorted((S35_PRELIFT_LONG / "audit").glob("*.privileged.json"))[:3]
    accepted = []
    for p in sample_audit_files:
        payload = json.loads(p.read_text(encoding="utf-8"))
        cid = payload["canonical_episode_id"]
        new_sid = hmac.new(live_salt, cid.encode("utf-8"), hashlib.sha256).hexdigest()
        payload["sample_id"] = new_sid
        payload["audit_salt_sha256"] = hashlib.sha256(live_salt).hexdigest()
        (live_root / "audit" / p.name).write_text(json.dumps(payload), encoding="utf-8")
        accepted.append({
            "sample_id": new_sid,
            "canonical_episode_id": cid,
            "seed": payload["seed"],
            "task": payload["task"],
            "stratum": payload["stratum"],
            "provenance_hash": payload["provenance_hash"],
        })
    (live_root / "accepted_manifest.json").write_text(json.dumps(accepted), encoding="utf-8")
    (live_root / "audit" / "stage35_audit_manifest.json").write_text(
        json.dumps({
            "total_accepted": len(accepted),
            "audit_salt_sha256": hashlib.sha256(live_salt).hexdigest(),
        }),
        encoding="utf-8",
    )
    verified_loader = AuditTargetLoader(live_root, audit_salt=live_salt)
    assert len(verified_loader.targets_by_sample_id) == 3
    with pytest.raises(ContractViolationError):
        AuditTargetLoader(live_root, audit_salt=os.urandom(32))

    # Verify both generated Stage 3.5 roots have self-consistent salt SHA-256 distinct from Stage 3.3 salt
    s33_salt_sha = hashlib.sha256(STAGE3_DETERMINISTIC_AUDIT_SALT).hexdigest()
    for root in (S35_PRELIFT_LONG, S35_FINAL_HOLDOUT):
        loader = AuditTargetLoader(root)
        manifest_sha = loader.audit_manifest["audit_salt_sha256"]
        assert len(manifest_sha) == 64 and manifest_sha != s33_salt_sha

    with pytest.raises(ContractViolationError):
        assert_no_privileged_keys_in_inputs({"step_mu_true": torch.zeros(1)})


# ---------------------------------------------------------------------------
# 10 & 11: Frozen B1-B4 & Stage 3.3 archive immutability
# ---------------------------------------------------------------------------

def test_frozen_phase34_checkpoints_and_stage33_archives_immutable() -> None:
    """Verify frozen Phase 3.4 B1-B4 checkpoints and all 1,000 Stage 3.3 archives remain untouched."""
    phase34_files = [p for p in PHASE34_CKPT_DIR.rglob("*") if p.is_file()]
    assert len(phase34_files) == 37

    report_path = PHASE34_CKPT_DIR / "phase34_training_report.json"
    expected_report_sha = "eda6781cdd075f02922ab00e89807b50530dd928a2eece5bb9dfbc058e3fa644"
    actual_report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert actual_report_sha == expected_report_sha

    for task in ("compliant_prelift", "peg_in_hole"):
        for regime in ("M0_passive", "M4_acmm"):
            for arch in ("b1_deterministic", "b2_probabilistic", "b3_ensemble"):
                ckpt = PHASE34_CKPT_DIR / task / regime / f"{arch}.pt"
                hybrid, _ = load_hybrid_checkpoint(str(ckpt))
                assert hybrid is not None
            calib_json = PHASE34_CKPT_DIR / task / regime / "b4_conformal_calibration.json"
            assert calib_json.is_file()
            json.loads(calib_json.read_text(encoding="utf-8"))

    # Stage 3.3 1,000 NPZ archives & manifest integrity
    s33_npzs = list((S33_ROOT / "training").glob("*.npz"))
    assert len(s33_npzs) == 1000
    assert (
        hashlib.sha256((S33_ROOT / "accepted_manifest.json").read_bytes()).hexdigest()
        == "45f961fce05b764968f699c9944c16c4f25c25a2a29e48a7c863c3997d51f2fd"
    )
    assert (
        hashlib.sha256((S33_ROOT / "stage34_episode_split_manifest.json").read_bytes()).hexdigest()
        == "6b34cd67029bb1fae6692b82a74571801797f7b84a89130955f6e8f3c7633c2d"
    )
    s33_audit = json.loads((S33_ROOT / "audit" / "stage3_3_audit_manifest.json").read_text(encoding="utf-8"))
    assert s33_audit["total_accepted"] == 1000


# ---------------------------------------------------------------------------
# 12: Training authorization enforcement
# ---------------------------------------------------------------------------

def test_training_authorization_lock_and_verification_state() -> None:
    """Verify TRAINING_PERMITTED=0, EXECUTION_AUTHORIZED=0, fail-closed barrier, and metrics lock."""
    assert TRAINING_PERMITTED == 0
    assert EXECUTION_AUTHORIZED == 0

    for cfg in (LOCKED_HCTT_CONFIG, EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG):
        runner = HCTTTrainingRunner(config=cfg, seed=3500)
        for fn in (runner.train, runner.run_training, runner.run_epoch, runner.step_optimizer, runner.backward):
            with pytest.raises(TrainingAuthorizationError):
                fn()
        assert runner.training_steps_executed == 0
        assert runner.parameter_updates_executed == 0

    metrics = json.loads((S35_ROOT / "stage35_verification_metrics.json").read_text(encoding="utf-8"))
    assert metrics["training_permitted"] == 0
    assert metrics["execution_authorized"] == 0
    assert metrics["gradient_steps"] == 0
    assert metrics["optimizer_updates"] == 0
    assert metrics["student_model_training"] == "NOT_EXECUTED"
    assert metrics["final_holdout_evaluated"] is False
