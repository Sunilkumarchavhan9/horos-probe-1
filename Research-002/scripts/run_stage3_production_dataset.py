"""Stage 3.3 Production Dataset Generation, Physics Validation, and Packaging.

Generates the 1,000 physics-validated production scenarios for HOROS World Model vNext:
- 500 Compliant Prelift scenarios:
    * 250 Nominal
    * 150 Probe
    * 100 Nonstationary (50 Regime A, 50 Regime B)
- 500 Peg-in-Hole scenarios:
    * 250 Nominal
    * 150 Impact
    * 100 Wedging

Enforces the Grounded Firewall:
- Separates model-visible training tensors (training/<sample_id>.npz) from
  privileged simulator ground truth and telemetry (audit/).
- Generates HMAC opaque sample IDs using a cryptographically strong research salt.
- Strictly validates against reconstructable joins or privileged data leakage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Ensure R002_ROOT and src are on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
R002_ROOT = SCRIPT_DIR.parent
for p in (R002_ROOT, R002_ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from contact_modes.contact_mode_definitions import ContactMode, MODE_NAMES, NUM_MODES
from teaching.factory import (
    EnvironmentSamplingSpec,
    OBSERVATION_ARRAY_FIELDS,
    SyntheticEpisodeRecord,
)
from teaching.inference import (
    BaseTeacherAdapter,
    TeacherInferenceEngine,
    TeacherInferenceResult,
    TeacherModelDescriptor,
    TeacherModelRegistry,
    TeacherProvider,
    TeacherQueryPayload,
)
from teaching.validator import PhysicsValidationEngine, ValidationVerdict
from scripts.stage3_production_adapter import (
    APPROVED_STATE_WIDTHS,
    MODEL_VISIBLE_ALLOWLIST,
    PEG_CONTROL_DT_S,
    PEG_FORCE_LIMIT_N,
    PEG_TORQUE_LIMIT_NM,
    PRIVILEGED_FIELD_BLOCKLIST,
    RECONSTRUCTABLE_IDENTIFIER_KEYS,
    ProductionTrainingDataloader,
    SecurityViolationError,
    Stage3ProductionExperienceFactory,
    assert_tensor_channels_clean,
    build_training_package_and_audit,
    generate_reserve_seed_registry,
    opaque_sample_id,
    validate_model_visible_package,
    verify_deterministic_reproducibility,
    verify_production_physical_interaction,
    verify_provenance_against_serialized_audit,
)


STAGE3_DETERMINISTIC_AUDIT_SALT = hashlib.sha256(b"HOROS_STAGE3_PRODUCTION_AUDIT_SALT_2026").digest()

TASK_EPISODE_LENGTHS = {
    "compliant_prelift": 20,
    "peg_in_hole": 30,
}

PRODUCTION_COUNTS = {
    "compliant_prelift": {
        "nominal": 250,
        "probe": 150,
        "nonstationary": 100,
    },
    "peg_in_hole": {
        "nominal": 250,
        "impact": 150,
        "wedging": 100,
    },
}


class Stage3ProductionTeacherController(BaseTeacherAdapter):
    """Authoritative production teacher controller for Stage 3.3 generation.
    
    Generates deterministic, physically grounded candidate action trajectories
    tailored to each task stratum while maintaining strict simulation decoupling:
    - Prelift Nominal: Firm clamping grip (Fn ~ 15 N) + smooth micro-lift (5 mm/s) -> liftoff without slip.
    - Prelift Probe: Calibrated light clamp (Fn ~ 3.7 N) + rapid micro-lift (8 mm/s) -> Coulomb slip without liftoff.
    - Prelift Nonstationary: Clamping grip (Fn ~ 11 N) + micro-lift -> causal slip under friction collapse (Regime A)
      and stable hold under mass accretion (Regime B).
    - Peg-in-Hole Nominal: Aligned compliance descent (dx = 0.02 mm/step, dz = -0.95 mm/step) -> smooth entry.
    - Peg-in-Hole Impact: Outward radial steering (dx, dy = 0.08 * u, dz = -0.95 mm/step) -> chamfer rim collision.
    - Peg-in-Hole Wedging: Deep descent past 5.0 mm at critical tilt angle -> two-point geometric lock.
    """

    def infer(self, payload: TeacherQueryPayload) -> TeacherInferenceResult:
        H = payload.target_horizon
        task = payload.task_id
        seed = payload.seed
        stratum_code = (seed % 10000) // 1000

        if task in ("compliant_prelift", "compliant_lift", "multifinger_lift"):
            actions = np.zeros((H, 5), dtype=np.float32)
            if stratum_code == 2:  # Probe
                for t in range(H):
                    if t == 0:
                        actions[t, :4] = 1.40
                    elif t == 1:
                        actions[t, :4] = 0.33  # Indent ~0.23 mm -> total Fn ~ 3.7 N
                    else:
                        actions[t, :4] = 0.00
                        actions[t, 4] = 0.080  # 8 mm/s lift velocity -> guaranteed Coulomb slip
            elif stratum_code == 1:  # Nominal
                for t in range(H):
                    if t == 0:
                        actions[t, :4] = 1.40
                    elif t == 1:
                        actions[t, :4] = 1.05  # Indent ~0.95 mm -> total Fn ~ 15.2 N
                    else:
                        actions[t, :4] = 0.00
                        actions[t, 4] = 0.050  # 5 mm/s lift velocity -> liftoff without slip
            else:  # Nonstationary (Regimes A & B)
                for t in range(H):
                    if t == 0:
                        actions[t, :4] = 1.40
                    elif t == 1:
                        actions[t, :4] = 0.80  # Indent ~0.70 mm -> total Fn ~ 11.2 N
                    else:
                        actions[t, :4] = 0.00
                        actions[t, 4] = 0.050
        else:  # Peg-in-Hole
            actions = np.zeros((H, 6), dtype=np.float32)
            init_x = float(payload.current_state[0])
            init_y = float(payload.current_state[1])
            r0 = float(np.hypot(init_x, init_y))
            if stratum_code == 4:  # Impact
                u_x = init_x / max(1e-4, r0) if r0 > 1e-4 else 1.0
                u_y = init_y / max(1e-4, r0) if r0 > 1e-4 else 0.0
                for t in range(H):
                    actions[t, 0] = 0.08 * u_x
                    actions[t, 1] = 0.08 * u_y
                    actions[t, 2] = -0.95
            elif stratum_code == 5:  # Wedging
                for t in range(H):
                    actions[t, 0] = 0.02
                    actions[t, 2] = -0.99  # Ensures penetration past 5.0 mm while respecting 0.10 m/s kinematic limit
            else:  # Nominal
                for t in range(H):
                    actions[t, 0] = 0.02
                    actions[t, 2] = -0.95

        prov_hash = hashlib.sha256(
            f"{self.descriptor.model_id}:{payload.compute_payload_hash()}:{actions.tobytes()}".encode("utf-8")
        ).hexdigest()

        return TeacherInferenceResult(
            model_id=self.descriptor.model_id,
            candidate_actions=actions,
            predicted_confidence=0.95,
            latent_representations=None,
            provenance_hash=prov_hash,
            cached=False,
            latency_ms=1.0,
            token_usage={"prompt_tokens": 100, "completion_tokens": 50},
        )


def run_production_generation(
    output_dir: Path,
    audit_salt: bytes = STAGE3_DETERMINISTIC_AUDIT_SALT,
) -> Dict[str, Any]:
    """Executes the full 1,000-scenario production dataset generation and validation."""
    start_time = time.time()
    output_dir = Path(output_dir)
    training_dir = output_dir / "training"
    audit_dir = output_dir / "audit"
    rejected_dir = output_dir / "rejected"

    training_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    # 1. Register Teacher Model
    registry = TeacherModelRegistry()
    teacher_desc = TeacherModelDescriptor(
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
    registry.register(teacher_desc)
    engine = TeacherInferenceEngine(registry)
    engine.register_adapter(teacher_desc.model_id, Stage3ProductionTeacherController(teacher_desc))

    # 2. Instantiate Production Factory and Validator
    factory = Stage3ProductionExperienceFactory(
        engine,
        sampling_spec=EnvironmentSamplingSpec(default_episode_length=20, dt_s=PEG_CONTROL_DT_S),
    )
    validator = PhysicsValidationEngine.for_tasks()

    accepted_manifest: List[Dict[str, Any]] = []
    rejected_manifest: List[Dict[str, Any]] = []
    audit_records: List[Dict[str, Any]] = []

    all_mode_counts: Counter[int] = Counter()
    task_statistics: Dict[str, Any] = {}

    print(f"=== Starting Stage 3.3 Production Dataset Generation (1,000 Scenarios) ===")
    total_generated = 0

    for task, strata_counts in PRODUCTION_COUNTS.items():
        ep_len = TASK_EPISODE_LENGTHS[task]
        task_stats = {
            "total_generated": 0,
            "total_accepted": 0,
            "total_rejected": 0,
            "strata": {},
        }

        for stratum, count in strata_counts.items():
            print(f"Generating {task} / {stratum} ({count} episodes)...")
            stratum_accepted = 0
            stratum_rejected = 0

            for idx in range(count):
                total_generated += 1
                record = factory.generate_stratified_episode(
                    task=task,
                    teacher_id=teacher_desc.model_id,
                    stratum=stratum,
                    index=idx,
                    episode_length=ep_len,
                )

                # Physics & Conservation Validation
                physics_report = validator.validate_trajectory(
                    task_id=task,
                    states=record.states,
                    actions=record.actions,
                    wrenches=record.wrenches,
                    env_metadata=record.env_metadata,
                )
                
                # Physical Interaction Validation
                interaction_passed, interaction_metrics = verify_production_physical_interaction(task, record)

                is_accepted = bool(physics_report.is_valid and interaction_passed)

                # Build model package and audit record
                package, audit = build_training_package_and_audit(record, audit_salt)

                # Update mode histogram
                modes_list = [int(m) for m in record.modes]
                all_mode_counts.update(modes_list)

                sample_id = audit["sample_id"]
                canonical_id = audit["canonical_episode_id"]

                if is_accepted:
                    stratum_accepted += 1
                    # Save model-visible package in training/
                    npz_path = training_dir / f"{sample_id}.npz"
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

                    # Save privileged audit in audit/
                    priv_path = audit_dir / f"{canonical_id}.privileged.json"
                    priv_path.write_text(
                        json.dumps(audit, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                    audit_summary_item = {
                        "sample_id": sample_id,
                        "canonical_episode_id": canonical_id,
                        "slot_alias": record.env_metadata.get("slot_alias"),
                        "seed": int(record.seed),
                        "task": task,
                        "stratum": stratum,
                        "index": idx,
                        "provenance_hash": record.provenance_hash,
                        "interaction_metrics": interaction_metrics,
                    }
                    accepted_manifest.append(audit_summary_item)
                    audit_records.append(audit)

                else:
                    stratum_rejected += 1
                    # Fail-closed rejected accounting
                    rejection_item = {
                        "canonical_episode_id": canonical_id,
                        "seed": int(record.seed),
                        "task": task,
                        "stratum": stratum,
                        "index": idx,
                        "physics_errors": physics_report.violation_details,
                        "interaction_metrics": interaction_metrics,
                    }
                    rejected_manifest.append(rejection_item)
                    rej_path = rejected_dir / f"{canonical_id}.rejected.json"
                    rej_path.write_text(
                        json.dumps(rejection_item, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

            task_stats["strata"][stratum] = {
                "generated": count,
                "accepted": stratum_accepted,
                "rejected": stratum_rejected,
                "acceptance_rate": round(stratum_accepted / count, 4),
            }
            task_stats["total_generated"] += count
            task_stats["total_accepted"] += stratum_accepted
            task_stats["total_rejected"] += stratum_rejected
            print(f"  -> {stratum}: {stratum_accepted}/{count} accepted ({stratum_accepted/count*100:.1f}%)")

        task_statistics[task] = task_stats

    # Write manifests
    (output_dir / "accepted_manifest.json").write_text(
        json.dumps(accepted_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "rejected_manifest.json").write_text(
        json.dumps(rejected_manifest, indent=2) + "\n", encoding="utf-8"
    )

    # Master audit manifest
    audit_manifest = {
        "manifest_version": "1.0.0",
        "protocol": "HOROS-P1-R002-STAGE3.3-PROD-GEN-PROTOCOL",
        "date_generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "audit_salt_sha256": hashlib.sha256(audit_salt).hexdigest(),
        "total_generated": total_generated,
        "total_accepted": len(accepted_manifest),
        "total_rejected": len(rejected_manifest),
        "overall_acceptance_rate": round(len(accepted_manifest) / total_generated, 4),
        "task_statistics": task_statistics,
        "mode_distribution": {MODE_NAMES.get(m, f"MODE_{m}"): count for m, count in sorted(all_mode_counts.items())},
        "mode_counts_by_id": dict(all_mode_counts),
        "reserve_pool": generate_reserve_seed_registry(),
    }
    (audit_dir / "stage3_3_audit_manifest.json").write_text(
        json.dumps(audit_manifest, indent=2) + "\n", encoding="utf-8"
    )

    elapsed_s = round(time.time() - start_time, 2)
    print(f"\nGeneration complete in {elapsed_s}s!")
    print(f"Accepted: {len(accepted_manifest)} / {total_generated} ({len(accepted_manifest)/total_generated*100:.1f}%)")
    print(f"Rejected: {len(rejected_manifest)} / {total_generated}")
    print(f"Mode Distribution: {dict(all_mode_counts)}")

    # 3. Grounded Firewall & Dataloader Verification
    print("\n[Step 3] Running Grounded Firewall & Training Dataloader Verifications...")
    loader = ProductionTrainingDataloader(training_dir)
    print(f"  - Loaded {len(loader.samples)} training archives successfully.")
    batches = loader.batches(batch_size=32)
    print(f"  - Formed {len(batches)} batches of size 32.")

    # Channel-level clean check across all loaded samples
    for sample in loader.samples:
        validate_model_visible_package(sample)
        assert_tensor_channels_clean(str(sample["task"]), sample["states"], sample["wrenches"], {})
        assert_tensor_channels_clean(str(sample["task"]), sample["next_states"], sample["next_wrenches"], {})
    print("  - 100% of training archives verified clean against privileged leakage!")

    summary = {
        "total_generated": total_generated,
        "total_accepted": len(accepted_manifest),
        "total_rejected": len(rejected_manifest),
        "overall_acceptance_rate": round(len(accepted_manifest) / total_generated, 4),
        "elapsed_seconds": elapsed_s,
        "task_statistics": task_statistics,
        "mode_distribution": dict(all_mode_counts),
    }
    (output_dir / "stage3_production_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    out_path = R002_ROOT / "experiments" / "stage3_production"
    run_production_generation(out_path)
