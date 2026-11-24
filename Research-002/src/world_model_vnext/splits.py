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

    if total != int(manifest["total_episodes"]):
        raise ContractViolationError(f"Split total {total} != manifest total {manifest['total_episodes']}")
    return True
