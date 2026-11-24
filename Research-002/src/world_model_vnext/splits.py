"""Immutable episode-level split generation and verification for Stage 3.4 HCTT-256.

Guarantees:
- Episode-level disjointness across train / val / calib / test (zero shared episodes or seeds).
- Stratified preservation across all 6 strata (including Prelift Regime A / Regime B parity).
- Zero overlap with held-out Phase 3 test seeds (PHASE3_SEED_POOLS), Stage 3.2 pilot seeds,
  and the 300 locked Stage 3.3 reserve seeds.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

import numpy as np

from .contracts import ContractViolationError


# Locked protected seed pools that MUST NEVER overlap production splits
PROTECTED_HISTORICAL_SEED_RANGES: Dict[str, Set[int]] = {
    "phase34_training_prelift": set(range(1000, 1060)),
    "phase34_training_peg": set(range(1100, 1160)),
    "phase34_calib_prelift": set(range(2000, 2060)),
    "phase34_calib_peg": set(range(2100, 2160)),
    "phase34_calib95_prelift": set(range(2200, 2260)),
    "phase34_calib95_peg": set(range(2300, 2360)),
    "phase35_offline_test": set(range(3000, 3120)),
    "phase35_closed_loop": set(range(6000, 6025)) | set(range(6100, 6125)) | set(range(6200, 6225)),
    "vnext_heldout_9200_9359": set(range(9200, 9360)),
    "stage32_pilot_prelift": set(range(3111000, 3111010)) | set(range(3112000, 3112006)) | set(range(3113000, 3113004)),
    "stage32_pilot_peg": set(range(3121000, 3121010)) | set(range(3124000, 3124006)) | set(range(3125000, 3125004)),
    "stage33_reserve_pool": (
        set(range(3401000, 3401050))
        | set(range(3402000, 3402050))
        | set(range(3403000, 3403050))
        | set(range(3411000, 3411050))
        | set(range(3414000, 3414050))
        | set(range(3415000, 3415050))
        | set(range(3390000, 3400000))
    ),
}

SPLIT_RATIOS: Dict[str, float] = {
    "train": 0.70,
    "val": 0.10,
    "calib": 0.10,
    "test": 0.10,
}


def build_episode_split_manifest(
    production_root: Path | str,
    split_seed: int = 3402026,
) -> Dict[str, Any]:
    """Builds an immutable stratified 70/10/10/10 episode-level split manifest."""
    root = Path(production_root)
    accepted_path = root / "accepted_manifest.json"
    audit_manifest_path = root / "audit" / "stage3_3_audit_manifest.json"

    if not accepted_path.is_file() or not audit_manifest_path.is_file():
        raise ContractViolationError("Missing accepted_manifest.json or audit manifest.")

    entries = json.loads(accepted_path.read_text(encoding="utf-8"))
    audit_manifest = json.loads(audit_manifest_path.read_text(encoding="utf-8"))

    # Group by (task, stratum, sub_regime) so Prelift nonstationary A/B stay 50/50 balanced
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_prod_seeds: Set[int] = set()
    for e in entries:
        seed = int(e["seed"])
        all_prod_seeds.add(seed)
        task = str(e["task"])
        stratum = str(e["stratum"])
        sub = ""
        if task == "compliant_prelift" and stratum == "nonstationary":
            sub = "_A" if int(e["index"]) % 2 == 0 else "_B"
        buckets[f"{task}::{stratum}{sub}"].append(e)

    # Verify zero collision against all protected historical and reserve seed pools
    overlap_report: Dict[str, int] = {}
    for pool_name, pool_seeds in PROTECTED_HISTORICAL_SEED_RANGES.items():
        inter = all_prod_seeds.intersection(pool_seeds)
        overlap_report[pool_name] = len(inter)
        if inter:
            raise ContractViolationError(
                f"Seed collision detected between Stage 3 production and '{pool_name}': {sorted(inter)[:5]}"
            )

    rng = np.random.RandomState(split_seed)
    splits: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "calib": [], "test": []}

    for bucket_key in sorted(buckets.keys()):
        bucket = sorted(buckets[bucket_key], key=lambda x: int(x["seed"]))
        perm = rng.permutation(len(bucket))
        shuffled = [bucket[i] for i in perm]
        n = len(shuffled)

        n_val = int(round(n * SPLIT_RATIOS["val"]))
        n_calib = int(round(n * SPLIT_RATIOS["calib"]))
        n_test = int(round(n * SPLIT_RATIOS["test"]))
        n_train = n - n_val - n_calib - n_test

        splits["train"].extend(shuffled[:n_train])
        splits["val"].extend(shuffled[n_train : n_train + n_val])
        splits["calib"].extend(shuffled[n_train + n_val : n_train + n_val + n_calib])
        splits["test"].extend(shuffled[n_train + n_val + n_calib :])

    # Canonical ordering and disjoint verification
    split_records: Dict[str, Any] = {}
    seen_sids: Dict[str, Set[str]] = {}
    seen_seeds: Dict[str, Set[int]] = {}
    for s_name, s_list in splits.items():
        s_sorted = sorted(s_list, key=lambda x: (x["task"], int(x["seed"])))
        sids = [str(x["sample_id"]) for x in s_sorted]
        seeds = [int(x["seed"]) for x in s_sorted]
        seen_sids[s_name] = set(sids)
        seen_seeds[s_name] = set(seeds)

        by_task_stratum = Counter(f"{x['task']}::{x['stratum']}" for x in s_sorted)
        split_records[s_name] = {
            "num_episodes": len(s_sorted),
            "sample_ids": sids,
            "seeds": seeds,
            "task_stratum_counts": dict(sorted(by_task_stratum.items())),
        }

    split_names = list(splits.keys())
    for i in range(len(split_names)):
        for j in range(i + 1, len(split_names)):
            a, b = split_names[i], split_names[j]
            if seen_sids[a] & seen_sids[b] or seen_seeds[a] & seen_seeds[b]:
                raise ContractViolationError(f"Episode/seed leak between splits '{a}' and '{b}'!")

    manifest = {
        "manifest_version": "stage34_episode_split_manifest_v1.0",
        "protocol": "HOROS-P1-R002-STAGE3.4-SPLIT-MANIFEST",
        "split_seed": split_seed,
        "split_granularity": "episode_level_strict",
        "total_episodes": len(entries),
        "accepted_manifest_sha256": hashlib.sha256(accepted_path.read_bytes()).hexdigest(),
        "audit_manifest_sha256": hashlib.sha256(audit_manifest_path.read_bytes()).hexdigest(),
        "reserve_pool_consumed": 0,
        "reserve_pool_locked_count": int(audit_manifest["reserve_pool"]["total_reserve_seeds"]),
        "protected_seed_overlap_counts": overlap_report,
        "cross_split_overlap_verified_zero": True,
        "splits": split_records,
    }
    return manifest


def verify_split_manifest_disjointness(manifest: Dict[str, Any]) -> bool:
    """Validates an existing split manifest for strict zero overlap across splits and protected pools."""
    splits = manifest["splits"]
    names = list(splits.keys())
    total = 0
    all_seeds: Set[int] = set()
    for i in range(len(names)):
        s_i = set(splits[names[i]]["sample_ids"])
        seed_i = set(splits[names[i]]["seeds"])
        if len(s_i) != len(splits[names[i]]["sample_ids"]) or len(seed_i) != len(splits[names[i]]["seeds"]):
            raise ContractViolationError(f"Duplicate entries within split '{names[i]}'")
        total += len(s_i)
        all_seeds.update(seed_i)
        for j in range(i + 1, len(names)):
            s_j = set(splits[names[j]]["sample_ids"])
            seed_j = set(splits[names[j]]["seeds"])
            if s_i & s_j or seed_i & seed_j:
                raise ContractViolationError(f"Overlap detected between splits '{names[i]}' and '{names[j]}'")

    for pool_name, pool_seeds in PROTECTED_HISTORICAL_SEED_RANGES.items():
        if all_seeds & pool_seeds:
            raise ContractViolationError(f"Split manifest overlaps protected seed pool '{pool_name}'")

    expected_total = (
        int(manifest["total_episodes"])
        if "total_episodes" in manifest
        else int(manifest["total_episodes_accounted"])
    )
    if total != expected_total:
        raise ContractViolationError(f"Split total {total} != manifest total {manifest['total_episodes']}")
    return True


STAGE35_PROTECTED_EXTRA_RANGES: Dict[str, Set[int]] = {
    **PROTECTED_HISTORICAL_SEED_RANGES,
    "stage33_production_all": (
        set(range(3311000, 3311250))
        | set(range(3312000, 3312150))
        | set(range(3313000, 3313100))
        | set(range(3321000, 3321250))
        | set(range(3324000, 3324150))
        | set(range(3325000, 3325100))
    ),
    "p5_t4r4_heldout_and_sweep": set(range(4900000, 4960000)),
}


def build_stage35_final_split_manifest(
    stage33_root: Path | str,
    stage34_split_manifest_path: Path | str,
    stage35_prelift_long_root: Path | str,
    stage35_final_holdout_root: Path | str,
    split_seed: int = 3502026,
) -> Dict[str, Any]:
    """Builds the leak-free Stage 3.5/4 master episode split manifest.

    Invariants enforced:
    1. Quarantines Stage 3.4's already-evaluated 100-episode test split
       ('exploratory_baseline_quarantined') — NEVER placed in train, val, calib, or final_test.
    2. Splits the 500 new T=40 Prelift episodes into 350 train / 75 val / 75 calib.
    3. Keeps non-quarantined Stage 3.3 Peg-in-Hole T=30 episodes (350 train / 50 val / 50 calib).
    4. Reserves the 120 fresh Stage 3.5 holdout episodes (60 Prelift T=40 + 60 Peg-in-Hole T=30)
       exclusively for 'final_test' (status: SEALED_UNTIL_AUTHORIZED_STAGE4_EVAL).
    5. Tracks non-quarantined legacy T=20 Prelift (450 episodes) in 'legacy_short_horizon_preserved'.
    6. Zero overlap across all buckets, reserve 300 seeds, pilot seeds, and P5-T4R4 4950001-4950600.
    """
    s33_root = Path(stage33_root)
    s34_manifest = json.loads(Path(stage34_split_manifest_path).read_text(encoding="utf-8"))
    verify_split_manifest_disjointness(s34_manifest)

    s33_entries = {str(e["sample_id"]): e for e in json.loads((s33_root / "accepted_manifest.json").read_text(encoding="utf-8"))}
    pre_long_entries = json.loads((Path(stage35_prelift_long_root) / "accepted_manifest.json").read_text(encoding="utf-8"))
    holdout_entries = json.loads((Path(stage35_final_holdout_root) / "accepted_manifest.json").read_text(encoding="utf-8"))

    # Verify new Stage 3.5 seeds have zero collision against ALL historical + Stage 3.3 + reserve + P5-T4R4 pools
    new_seeds = {int(e["seed"]) for e in pre_long_entries} | {int(e["seed"]) for e in holdout_entries}
    for pool_name, pool_seeds in STAGE35_PROTECTED_EXTRA_RANGES.items():
        overlap = new_seeds & pool_seeds
        if overlap:
            raise ContractViolationError(
                f"Stage 3.5 seed allocation collides with '{pool_name}': {sorted(overlap)[:5]}"
            )
    if len({int(e["seed"]) for e in pre_long_entries} & {int(e["seed"]) for e in holdout_entries}) > 0:
        raise ContractViolationError("Overlap between stage35 prelift_long and final_holdout seeds.")

    # Partition 500 Prelift T=40 (250 nom, 150 probe, 100 nonstat[50A+50B]) into 350 train / 75 val / 75 calib (70% / 15% / 15%)
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in pre_long_entries:
        stratum = str(e["stratum"])
        sub = "_A" if (stratum == "nonstationary" and int(e["index"]) % 2 == 0) else ("_B" if stratum == "nonstationary" else "")
        buckets[f"{stratum}{sub}"].append(e)

    rng = np.random.RandomState(split_seed)
    pre_long_splits: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "calib": []}
    for b_key in sorted(buckets.keys()):
        b_list = sorted(buckets[b_key], key=lambda x: int(x["seed"]))
        perm = rng.permutation(len(b_list))
        shuffled = [b_list[i] for i in perm]
        n = len(shuffled)
        n_val = int(round(n * 0.15))
        n_calib = int(round(n * 0.15))
        n_train = n - n_val - n_calib
        pre_long_splits["train"].extend(shuffled[:n_train])
        pre_long_splits["val"].extend(shuffled[n_train : n_train + n_val])
        pre_long_splits["calib"].extend(shuffled[n_train + n_val :])

    # Collect clean non-quarantined Stage 3.3 Peg-in-Hole entries from Stage 3.4 splits
    quarantined_entries = [s33_entries[sid] for sid in s34_manifest["splits"]["test"]["sample_ids"]]
    peg_train = [s33_entries[sid] for sid in s34_manifest["splits"]["train"]["sample_ids"] if s33_entries[sid]["task"] == "peg_in_hole"]
    peg_val = [s33_entries[sid] for sid in s34_manifest["splits"]["val"]["sample_ids"] if s33_entries[sid]["task"] == "peg_in_hole"]
    peg_calib = [s33_entries[sid] for sid in s34_manifest["splits"]["calib"]["sample_ids"] if s33_entries[sid]["task"] == "peg_in_hole"]
    legacy_prelift_20 = [
        s33_entries[sid]
        for s_name in ("train", "val", "calib")
        for sid in s34_manifest["splits"][s_name]["sample_ids"]
        if s33_entries[sid]["task"] == "compliant_prelift"
    ]

    all_buckets: Dict[str, List[Dict[str, Any]]] = {
        "train": pre_long_splits["train"] + peg_train,
        "val": pre_long_splits["val"] + peg_val,
        "calib": pre_long_splits["calib"] + peg_calib,
        "final_test": holdout_entries,
        "exploratory_baseline_quarantined": quarantined_entries,
        "legacy_short_horizon_preserved": legacy_prelift_20,
    }

    split_records: Dict[str, Any] = {}
    sid_sets: Dict[str, Set[str]] = {}
    seed_sets: Dict[str, Set[int]] = {}
    for name, items in all_buckets.items():
        ordered = sorted(items, key=lambda x: (x["task"], int(x["seed"])))
        sids = [str(x["sample_id"]) for x in ordered]
        seeds = [int(x["seed"]) for x in ordered]
        if len(set(sids)) != len(sids) or len(set(seeds)) != len(seeds):
            raise ContractViolationError(f"Duplicate entries inside partition '{name}'")
        sid_sets[name] = set(sids)
        seed_sets[name] = set(seeds)
        split_records[name] = {
            "num_episodes": len(ordered),
            "sample_ids": sids,
            "seeds": seeds,
            "task_stratum_counts": dict(sorted(Counter(f"{x['task']}::{x['stratum']}" for x in ordered).items())),
            "sealed": name == "final_test",
            "quarantined_exploratory": name == "exploratory_baseline_quarantined",
        }

    names = list(all_buckets.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if sid_sets[a] & sid_sets[b] or seed_sets[a] & seed_sets[b]:
                raise ContractViolationError(f"Partition leak detected between '{a}' and '{b}'!")

    return {
        "manifest_version": "stage35_final_split_manifest_v1.0",
        "protocol": "HOROS-P1-R002-STAGE3.5-FINAL-SPLIT-MANIFEST",
        "split_seed": split_seed,
        "split_granularity": "episode_level_strict",
        "total_episodes_accounted": sum(len(v) for v in all_buckets.values()),
        "reserve_pool_consumed": 0,
        "reserve_pool_locked_count": 300,
        "p5_t4r4_heldout_overlap_count": len(new_seeds & set(range(4950001, 4950601))),
        "cross_partition_overlap_verified_zero": True,
        "final_test_status": "SEALED_UNTIL_AUTHORIZED_STAGE4_EVAL",
        "splits": split_records,
    }
