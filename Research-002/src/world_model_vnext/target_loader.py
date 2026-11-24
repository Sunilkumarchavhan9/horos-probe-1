"""Controlled audit-target loader keeping privileged ground truth separate from model inputs.

Reads exclusively from experiments/stage3_production/audit/ and manifest provenance records:
- Joins training samples to audit targets strictly via opaque HMAC sample_id.
- Validates accepted_manifest.json and stage3_3_audit_manifest.json provenance.
- Fails closed on missing, duplicate, mismatched, or out-of-vocabulary records.
- Never modifies model input dictionaries; supplies supervised targets strictly to loss/eval modules.
"""

from __future__ import annotations

import hmac
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch

from .contracts import (
    ContractViolationError,
    LOCKED_HCTT_CONFIG,
    SUPPORTED_OPERATIONAL_MODE_IDS,
    UNSUPPORTED_DATASET_MODE_IDS,
    get_task_interface,
)


@dataclass(frozen=True)
class AuditEpisodeTargets:
    """Verified supervised targets for one episode (never exposed to model.forward)."""

    sample_id: str
    canonical_episode_id: str
    seed: int
    task: str
    stratum: str
    regime: str
    provenance_hash: str
    modes: np.ndarray            # Shape (T,), int64 in {0..4}
    step_mu_true: np.ndarray     # Shape (T,), float32
    step_mass_kg: np.ndarray     # Shape (T,), float32
    initial_mu_true: float
    initial_mass_kg: float


class AuditTargetLoader:
    """Fail-closed loader for separately controlled privileged training/evaluation targets."""

    def __init__(
        self,
        production_root: Path | str,
        audit_salt: Optional[bytes] = None,
    ):
        self.production_root = Path(production_root)
        self.audit_dir = self.production_root / "audit"
        self.accepted_manifest_path = self.production_root / "accepted_manifest.json"
        self.audit_manifest_path = self.audit_dir / "stage3_3_audit_manifest.json"

        if not self.audit_dir.is_dir():
            raise ContractViolationError(f"Audit directory missing: {self.audit_dir}")
        if not self.accepted_manifest_path.is_file():
            raise ContractViolationError(f"Accepted manifest missing: {self.accepted_manifest_path}")
        if not self.audit_manifest_path.is_file():
            raise ContractViolationError(f"Master audit manifest missing: {self.audit_manifest_path}")

        self.audit_manifest: Dict[str, Any] = json.loads(self.audit_manifest_path.read_text(encoding="utf-8"))
        if audit_salt is not None:
            expected_salt_sha = self.audit_manifest.get("audit_salt_sha256")
            actual_salt_sha = hashlib.sha256(audit_salt).hexdigest()
            if expected_salt_sha != actual_salt_sha:
                raise ContractViolationError(
                    f"Provided audit_salt SHA-256 ({actual_salt_sha}) does not match audit manifest ({expected_salt_sha})."
                )
        self.audit_salt = audit_salt

        self.targets_by_sample_id: Dict[str, AuditEpisodeTargets] = {}
        self.mode_histogram: Dict[int, int] = {m: 0 for m in SUPPORTED_OPERATIONAL_MODE_IDS}
        self._load_and_verify()

    def _load_and_verify(self) -> None:
        manifest_entries = json.loads(self.accepted_manifest_path.read_text(encoding="utf-8"))
        expected_total = int(self.audit_manifest.get("total_accepted", -1))
        if len(manifest_entries) != expected_total:
            raise ContractViolationError(
                f"accepted_manifest count ({len(manifest_entries)}) != audit_manifest total_accepted ({expected_total})"
            )

        seen_sample_ids = set()
        seen_seeds = set()
        seen_canon_ids = set()

        for entry in manifest_entries:
            sid = str(entry["sample_id"])
            cid = str(entry["canonical_episode_id"])
            seed = int(entry["seed"])
            task = str(entry["task"])
            stratum = str(entry["stratum"])
            prov_hash = str(entry["provenance_hash"])
            spec = get_task_interface(task)

            if sid in seen_sample_ids or cid in seen_canon_ids or seed in seen_seeds:
                raise ContractViolationError(f"Duplicate episode identifier detected: sid={sid}, cid={cid}, seed={seed}")
            seen_sample_ids.add(sid)
            seen_canon_ids.add(cid)
            seen_seeds.add(seed)

            if self.audit_salt is not None:
                recomputed_sid = hmac.new(self.audit_salt, cid.encode("utf-8"), hashlib.sha256).hexdigest()
                if recomputed_sid != sid:
                    raise ContractViolationError(
                        f"HMAC verification failed for {cid}: expected {recomputed_sid}, found {sid}"
                    )

            priv_path = self.audit_dir / f"{cid}.privileged.json"
            if not priv_path.is_file():
                raise ContractViolationError(f"Missing privileged audit file: {priv_path}")

            priv = json.loads(priv_path.read_text(encoding="utf-8"))
            if str(priv.get("sample_id")) != sid:
                raise ContractViolationError(f"sample_id mismatch in {priv_path.name}")
            if str(priv.get("canonical_episode_id")) != cid:
                raise ContractViolationError(f"canonical_episode_id mismatch in {priv_path.name}")
            if int(priv.get("seed", -1)) != seed:
                raise ContractViolationError(f"seed mismatch in {priv_path.name}")
            if str(priv.get("task")) != task:
                raise ContractViolationError(f"task mismatch in {priv_path.name}")
            if str(priv.get("provenance_hash")) != prov_hash:
                raise ContractViolationError(f"provenance_hash mismatch in {priv_path.name}")

            if priv.get("audit_salt_sha256") != self.audit_manifest.get("audit_salt_sha256"):
                raise ContractViolationError(f"audit_salt_sha256 mismatch in {priv_path.name}")

            modes = np.asarray(priv["modes"], dtype=np.int64)
            T = spec.canonical_episode_length
            if modes.shape != (T,):
                raise ContractViolationError(
                    f"Episode {cid} mode length {modes.shape} != expected ({T},)"
                )

            unique_modes = set(int(m) for m in np.unique(modes))
            unsupported = unique_modes.intersection(UNSUPPORTED_DATASET_MODE_IDS)
            invalid = unique_modes - SUPPORTED_OPERATIONAL_MODE_IDS
            if unsupported or invalid:
                raise ContractViolationError(
                    f"Episode {cid} contains modes outside 5-class operational contract (0..4): {sorted(unique_modes)}"
                )

            for m_val in modes:
                self.mode_histogram[int(m_val)] += 1

            env_meta = priv.get("env_metadata", {})
            init_mu = float(priv.get("friction_mu", env_meta.get("initial_mu_true", 0.0)))
            init_mass = float(priv.get("mass_kg", env_meta.get("initial_mass_kg", 0.0)))
            if not (np.isfinite(init_mu) and init_mu > 0.0):
                raise ContractViolationError(f"Invalid initial friction_mu {init_mu} in {cid}")
            if not (np.isfinite(init_mass) and init_mass > 0.0):
                raise ContractViolationError(f"Invalid initial mass_kg {init_mass} in {cid}")

            raw_step_mu = env_meta.get("step_mu_true")
            if raw_step_mu is not None and len(raw_step_mu) == T:
                step_mu = np.asarray(raw_step_mu, dtype=np.float32)
            else:
                step_mu = np.full((T,), init_mu, dtype=np.float32)

            raw_step_mass = env_meta.get("step_object_mass_kg")
            if raw_step_mass is not None and len(raw_step_mass) == T:
                step_mass = np.asarray(raw_step_mass, dtype=np.float32)
            else:
                step_mass = np.full((T,), init_mass, dtype=np.float32)

            if not (np.all(np.isfinite(step_mu)) and np.all(step_mu > 0.0)):
                raise ContractViolationError(f"Non-finite or non-positive step_mu_true in {cid}")
            if not (np.all(np.isfinite(step_mass)) and np.all(step_mass > 0.0)):
                raise ContractViolationError(f"Non-finite or non-positive step_mass_kg in {cid}")

            self.targets_by_sample_id[sid] = AuditEpisodeTargets(
                sample_id=sid,
                canonical_episode_id=cid,
                seed=seed,
                task=task,
                stratum=stratum,
                regime=str(env_meta.get("regime", "nominal")),
                provenance_hash=prov_hash,
                modes=modes,
                step_mu_true=step_mu,
                step_mass_kg=step_mass,
                initial_mu_true=init_mu,
                initial_mass_kg=init_mass,
            )

    def get_batch_targets(
        self,
        sample_ids: Sequence[str],
        anchor_steps: Sequence[int],
        future_mask: torch.Tensor,
        horizon: int = LOCKED_HCTT_CONFIG.max_future_horizon,
    ) -> Dict[str, Any]:
        """Joins clean sample_ids to privileged supervised loss targets (NEVER model inputs)."""
        if len(sample_ids) != len(anchor_steps) or future_mask.shape != (len(sample_ids), horizon):
            raise ContractViolationError("Batch target join dimension mismatch.")

        B = len(sample_ids)
        target_modes = np.full((B, horizon), -100, dtype=np.int64)
        target_mu = np.zeros((B,), dtype=np.float32)
        target_mass = np.zeros((B,), dtype=np.float32)
        seeds: List[int] = []
        strata: List[str] = []
        regimes: List[str] = []

        mask_np = future_mask.detach().cpu().numpy().astype(bool)

        for i, (sid, t) in enumerate(zip(sample_ids, anchor_steps)):
            if sid not in self.targets_by_sample_id:
                raise ContractViolationError(f"Unknown sample_id '{sid}' missing from verified audit manifest.")
            rec = self.targets_by_sample_id[sid]
            T = rec.modes.shape[0]
            if not (0 <= t < T):
                raise ContractViolationError(f"Anchor step {t} out of bounds [0, {T}) for {sid}")

            valid_n = int(np.sum(mask_np[i]))
            expected_n = min(horizon, T - t)
            if valid_n != expected_n:
                raise ContractViolationError(
                    f"Future mask length {valid_n} != valid remaining steps {expected_n} for {sid} at t={t}"
                )

            target_modes[i, :valid_n] = rec.modes[t : t + valid_n]
            target_mu[i] = float(rec.step_mu_true[t])
            target_mass[i] = float(rec.step_mass_kg[t])
            seeds.append(rec.seed)
            strata.append(rec.stratum)
            regimes.append(rec.regime)

        # Distillation targets remain strictly None unless real teacher hidden representations exist
        return {
            "target_modes": torch.from_numpy(target_modes),
            "target_mu": torch.from_numpy(target_mu),
            "target_mass": torch.from_numpy(target_mass),
            "teacher_representations": None,
            "distillation_target_available": False,
            "seeds": seeds,
            "strata": strata,
            "regimes": regimes,
        }
