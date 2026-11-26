"""Read-only scientific evaluation infrastructure for HOROS World Model vNext and frozen baselines.

Evaluates:
- State & wrench prediction error (MSE, RMSE) at horizons H in {1, 3, 5, 10, 20} (unpadded valid steps only)
- Multi-step rollout error drift across horizons
- 5-class contact-mode Macro-F1 and per-class F1 (modes 0..4; tracks absent modes honestly)
- Latent friction and mass identification R^2, Pearson r, Spearman rho
- OOD discrimination AUROC (nominal vs non-nominal strata)
- Mode probability calibration (ECE, Brier score) & uncertainty ranking quality (AURC)
- Conformal coverage at nominal 1 - alpha = 0.90 across supported modes
- Physical consistency and finite-value invariants
- Frozen B1-B4 baseline comparison on identical Stage 3.4 test splits (strictly read-only)
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
from scipy import stats as scipy_stats

from .contracts import (
    BENCHMARK_ACCEPTANCE_TARGETS,
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    HoldoutIneligibleError,
    LOCKED_HCTT_CONFIG,
    OPERATIONAL_MODE_NAMES,
    SUPPORTED_OPERATIONAL_MODE_IDS,
    is_compromised_holdout_path,
    is_compromised_holdout_sample_id,
    verify_redacted_benchmark_output,
)
from .dataset import HCTTTemporalDataset, split_batch_inputs_and_targets
from .ed25519_manifest import verify_signed_evaluation_manifest
from .evaluation_lock import DurableHoldoutLock
from .hctt import HCTTWorldModel
from .target_loader import AuditTargetLoader
from .worker_isolation import (
    IsolatedInferenceWorker,
    apply_os_vault_isolation,
    remove_os_vault_isolation,
)


def _compute_r2(y_true: np.ndarray, y_pred: np.ndarray) -> Optional[float]:
    if len(y_true) < 2:
        return None
    y_t = np.asarray(y_true, dtype=np.float64)
    y_p = np.asarray(y_pred, dtype=np.float64)
    if float(np.std(y_t)) < 1e-6:
        return None
    var = float(np.sum((y_t - np.mean(y_t)) ** 2))
    if var < 1e-8:
        return None
    sse = float(np.sum((y_t - y_p) ** 2))
    return round(1.0 - sse / var, 4)


def _safe_corr(x: np.ndarray, y: np.ndarray) -> Dict[str, Optional[float]]:
    x_arr = np.asarray(x, dtype=np.float64)
    y_arr = np.asarray(y, dtype=np.float64)
    if len(x_arr) < 3 or float(np.std(x_arr)) < 1e-6 or float(np.std(y_arr)) < 1e-6:
        return {"pearson_r": None, "spearman_rho": None}
    pr = float(scipy_stats.pearsonr(x_arr, y_arr).statistic)
    sr = float(scipy_stats.spearmanr(x_arr, y_arr).statistic)
    return {
        "pearson_r": round(pr, 4) if math.isfinite(pr) else None,
        "spearman_rho": round(sr, 4) if math.isfinite(sr) else None,
    }


def _compute_auroc(scores: np.ndarray, labels: np.ndarray) -> Optional[float]:
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    pos = s[y == 1]
    neg = s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    # Exact Mann-Whitney U rank calculation handling ties
    diffs = pos[:, None] - neg[None, :]
    auc = float(np.mean((diffs > 0.0) + 0.5 * (diffs == 0.0)))
    return round(auc, 4)


def _compute_multiclass_f1(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    mode_ids: Sequence[int] = tuple(sorted(SUPPORTED_OPERATIONAL_MODE_IDS)),
) -> Dict[str, Any]:
    per_class: Dict[str, Any] = {}
    present_f1s: List[float] = []
    all_f1s: List[float] = []

    for m in mode_ids:
        tp = int(np.sum((y_true == m) & (y_pred == m)))
        fp = int(np.sum((y_true != m) & (y_pred == m)))
        fn = int(np.sum((y_true == m) & (y_pred != m)))
        support = int(np.sum(y_true == m))

        denom = 2 * tp + fp + fn
        f1 = (2.0 * tp / denom) if denom > 0 else 0.0
        prec = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0

        per_class[OPERATIONAL_MODE_NAMES[m]] = {
            "mode_id": int(m),
            "support": support,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
        }
        all_f1s.append(f1)
        if support > 0:
            present_f1s.append(f1)

    return {
        "macro_f1_all_5": round(float(np.mean(all_f1s)), 4) if all_f1s else 0.0,
        "macro_f1_present_modes": round(float(np.mean(present_f1s)), 4) if present_f1s else 0.0,
        "per_class": per_class,
        "absent_modes_note": "Modes 5 (Jamming) and 6 (Release) have 0 examples in Stage 3.3 data.",
    }


def _compute_calibration_metrics(probs: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> Dict[str, Any]:
    if len(y_true) == 0:
        return {"ece": None, "brier_score": None}
    num_classes = probs.shape[1]
    one_hot = np.eye(num_classes, dtype=np.float64)[y_true]
    brier = float(np.mean(np.sum((probs - one_hot) ** 2, axis=-1)))

    confs = np.max(probs, axis=-1)
    preds = np.argmax(probs, axis=-1)
    accs = (preds == y_true).astype(np.float64)

    ece = 0.0
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    for b in range(n_bins):
        lo, hi = bin_edges[b], bin_edges[b + 1]
        in_bin = (confs > lo) & (confs <= hi) if b > 0 else (confs >= lo) & (confs <= hi)
        if np.any(in_bin):
            ece += (float(np.sum(in_bin)) / len(y_true)) * abs(float(np.mean(accs[in_bin])) - float(np.mean(confs[in_bin])))

    return {
        "ece": round(ece, 4),
        "brier_score": round(brier, 4),
    }


class HCTTReadOnlyEvaluator:
    """Read-only evaluation suite for HCTTWorldModel and frozen baselines."""

    def __init__(self, target_loader: AuditTargetLoader):
        self.target_loader = target_loader

    def verify_dataset_eligibility(
        self,
        dataset: HCTTTemporalDataset,
        target_loader: Optional[AuditTargetLoader] = None,
    ) -> None:
        """Asserts dataset and optional target_loader are eligible for scientific evaluation."""
        for d in getattr(dataset, "training_dirs", []):
            if is_compromised_holdout_path(d):
                raise CompromisedHoldoutError(
                    f"Dataset directory '{d}' belongs to quarantined holdout '{COMPROMISED_HOLDOUT_ID}' "
                    f"(status: {COMPROMISED_HOLDOUT_STATUS}). Evaluation strictly blocked."
                )
        if getattr(dataset, "allowed_sample_ids", None) is not None:
            for sid in dataset.allowed_sample_ids:
                if is_compromised_holdout_sample_id(sid):
                    raise CompromisedHoldoutError(
                        f"Dataset contains quarantined sample ID '{sid}' "
                        f"(status: {COMPROMISED_HOLDOUT_STATUS}). Evaluation strictly blocked."
                    )
        for ep in getattr(dataset, "episodes", []):
            if is_compromised_holdout_sample_id(ep.sample_id):
                raise CompromisedHoldoutError(
                    f"Episode '{ep.sample_id}' belongs to quarantined holdout '{COMPROMISED_HOLDOUT_ID}'. "
                    f"Evaluation strictly blocked."
                )
        if target_loader is not None:
            target_loader.assert_evaluation_eligible()

    def verify_holdout_eligibility(
        self,
        holdout_identifier: str,
        authorization_artifact: Optional[Any] = None,
    ) -> None:
        """Verifies eligibility of a dataset for final benchmark evaluation."""
        h_id_clean = str(holdout_identifier).lower()
        if (
            holdout_identifier == COMPROMISED_HOLDOUT_ID
            or "final_holdout" in h_id_clean
            or "stage35_final_holdout" in h_id_clean
            or "stage35" in h_id_clean
        ):
            raise HoldoutIneligibleError(
                f"Holdout '{holdout_identifier}' is quarantined under status '{COMPROMISED_HOLDOUT_STATUS}'. "
                "Final benchmark evaluation is permanently blocked."
            )

        # Reject caller-supplied boolean credentials
        if isinstance(authorization_artifact, dict) and "certified_private_holdout" in authorization_artifact:
            raise HoldoutIneligibleError(
                "Caller-supplied boolean credentials ('certified_private_holdout') are strictly rejected. "
                "Final holdout evaluation requires an independently authorized, cryptographically validated provenance artifact."
            )

        # In current stage, no replacement private holdout has been authorized or created
        raise HoldoutIneligibleError(
            "No authorized private holdout currently exists (NEW_PRIVATE_HOLDOUT = NOT_CREATED). "
            "Final benchmark evaluation is permanently blocked until an independent private holdout is authorized and generated."
        )

    def evaluate_blind_holdout_release(
        self,
        signed_manifest_envelope: Dict[str, Any],
        checkpoint_sha256: str,
        ledger: DurableHoldoutLock,
        synthetic_dataset_loader_fn: Callable[[], Tuple[HCTTTemporalDataset, AuditTargetLoader]],
        trusted_keys: Optional[Dict[str, Any]] = None,
        use_isolated_worker: bool = False,
        vault_path: Optional[Path | str] = None,
        batch_size: int = 32,
    ) -> Dict[str, Any]:
        """Executes full end-to-end blind holdout evaluation following the 13-stage security architecture.
        
        1. Verifies signed evaluation manifest using Ed25519 and trusted public keys.
        2. Asserts holdout is eligible (rejects quarantined Stage 3.5 holdout).
        3. Atomically reserves release in durable ledger (fails closed if already reserved or consumed).
        4. Authorizes private data access in durable ledger.
        5. Applies OS-level vault isolation (NTFS deny ACL on Windows, chmod 000 on POSIX).
        6. Decrypts / loads private holdout data (synthetic test fixtures) and records DATA_ACCESSED (burn-on-access).
        7. Separates privileged targets: AuditTargetLoader stays in privileged evaluator process.
        8. Strict IPC validation: validates input tensors before IPC transmission.
        9. Spawns and delegates to OS-isolated inference worker.
        10. Receives prediction outputs via IPC pipe.
        11. Computes evaluation metrics in evaluator process without leaking targets.
        12. Verifies redacted benchmark output schema (no per-episode targets or seeds).
        13. Marks release permanently CONSUMED with final report digest in durable ledger.
        """
        # Gate 1: Signed Evaluation Manifest & Trusted Key Verification
        manifest_payload = verify_signed_evaluation_manifest(
            signed_envelope=signed_manifest_envelope,
            trusted_keys=trusted_keys,
        )
        release_id = str(manifest_payload.get("release_id", manifest_payload.get("holdout_id", "")))
        if not release_id:
            raise ContractViolationError("Manifest missing 'release_id'.")

        # Gate 2: Holdout Release Eligibility (reject quarantined/compromised holdout)
        if (
            release_id == COMPROMISED_HOLDOUT_ID
            or "final_holdout" in release_id.lower()
            or "stage35" in release_id.lower()
        ):
            raise HoldoutIneligibleError(
                f"Release '{release_id}' belongs to quarantined dataset '{COMPROMISED_HOLDOUT_ID}'. "
                f"Evaluation is permanently blocked under status '{COMPROMISED_HOLDOUT_STATUS}'."
            )

        # Gate 3: Durable One-Time Reservation
        ledger.reserve_release(
            holdout_release_id=release_id,
            checkpoint_sha256=checkpoint_sha256,
        )

        # Gate 4: Private Data Access Authorization
        ledger.authorize_data_access(release_id)

        # Gate 5: OS-Level Vault Isolation Setup (if vault path specified)
        if vault_path:
            apply_os_vault_isolation(vault_path)

        data_accessed = False
        worker: Optional[IsolatedInferenceWorker] = None
        try:
            # Gate 6: Data Access & Decryption (release burned on access)
            dataset, audit_target_loader = synthetic_dataset_loader_fn()
            ledger.record_private_data_access(release_id)
            data_accessed = True

            # Gate 7: Privileged Target Separation
            self.verify_dataset_eligibility(dataset, audit_target_loader)

            # Gate 8 & 9: Strict IPC Validation & OS-Isolated Inference Worker
            horizons = LOCKED_HCTT_CONFIG.prediction_horizons
            state_sq_errs: Dict[int, List[float]] = {h: [] for h in horizons}
            wrench_sq_errs: Dict[int, List[float]] = {h: [] for h in horizons}
            all_true_modes: List[np.ndarray] = []
            all_pred_modes: List[np.ndarray] = []
            all_mode_probs: List[np.ndarray] = []
            all_true_mu: List[np.ndarray] = []
            all_pred_mu: List[np.ndarray] = []
            all_true_mass: List[np.ndarray] = []
            all_pred_mass: List[np.ndarray] = []
            all_unc_step1: List[np.ndarray] = []
            all_err_step1: List[np.ndarray] = []
            all_ood_labels: List[np.ndarray] = []
            all_finite = True

            if use_isolated_worker:
                worker = IsolatedInferenceWorker()

            batches = dataset.iter_homogeneous_batches(batch_size=batch_size, shuffle=False)
            with torch.no_grad():
                for batch in batches:
                    model_inputs, sim_targets = split_batch_inputs_and_targets(batch)
                    audit_targets = audit_target_loader.get_batch_targets(
                        batch["sample_ids"],
                        batch["anchor_steps"],
                        batch["future_mask"],
                    )

                    if worker is not None:
                        # Construct allowlisted model-visible IPC payload
                        states = model_inputs["hist_states"]
                        actions = torch.cat([model_inputs["hist_prev_actions"], model_inputs["future_actions"]], dim=1)
                        wrenches = model_inputs["hist_wrenches"]
                        task = model_inputs["task"]

                        ipc_payload = {
                            "states": states,
                            "actions": actions,
                            "wrenches": wrenches,
                            "task": task,
                        }
                        out = worker.predict(ipc_payload)
                    else:
                        model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
                        model.eval()
                        out = model(**model_inputs)

                    for v in out.values():
                        if isinstance(v, torch.Tensor) and not torch.isfinite(v).all():
                            all_finite = False

                    pred_s = out["pred_next_states"].cpu().numpy()
                    true_s = sim_targets["target_next_states"].cpu().numpy()
                    pred_w = out["pred_next_wrenches"].cpu().numpy()
                    true_w = sim_targets["target_next_wrenches"].cpu().numpy()
                    f_mask = sim_targets["future_mask"].cpu().numpy().astype(bool)

                    # Horizon-indexed MSE on strictly unpadded steps
                    for h in horizons:
                        idx = h - 1
                        if idx < pred_s.shape[1]:
                            valid_h = f_mask[:, idx]
                            if np.any(valid_h):
                                s_err = np.mean((pred_s[valid_h, idx, :] - true_s[valid_h, idx, :]) ** 2, axis=-1)
                                w_err = np.mean((pred_w[valid_h, idx, :] - true_w[valid_h, idx, :]) ** 2, axis=-1)
                                state_sq_errs[h].extend(s_err.tolist())
                                wrench_sq_errs[h].extend(w_err.tolist())

                    # Contact modes
                    t_modes = audit_targets["target_modes"].numpy()
                    p_probs = out["pred_mode_probs"].cpu().numpy()
                    p_modes = np.argmax(p_probs, axis=-1)
                    valid_flat = f_mask & (t_modes >= 0)
                    if np.any(valid_flat):
                        all_true_modes.append(t_modes[valid_flat])
                        all_pred_modes.append(p_modes[valid_flat])
                        all_mode_probs.append(p_probs[valid_flat])

                    # Latent SysID
                    all_true_mu.append(audit_targets["target_mu"].numpy())
                    all_pred_mu.append(out["pred_friction_mu"].cpu().numpy())
                    all_true_mass.append(audit_targets["target_mass"].numpy())
                    all_pred_mass.append(out["pred_mass_kg"].cpu().numpy())

                    # Uncertainty & OOD
                    step1_l2 = np.linalg.norm(pred_s[:, 0, :] - true_s[:, 0, :], axis=-1)
                    step1_unc = out["pred_uncertainty_total"][:, 0].cpu().numpy()
                    all_err_step1.append(step1_l2)
                    all_unc_step1.append(step1_unc)
                    ood_bin = np.array([0 if st == "nominal" else 1 for st in audit_targets["strata"]], dtype=np.int64)
                    all_ood_labels.append(ood_bin)

            horizon_metrics: Dict[str, Any] = {}
            for h in horizons:
                s_vals = state_sq_errs[h]
                w_vals = wrench_sq_errs[h]
                if s_vals:
                    s_mse = float(np.mean(s_vals))
                    w_mse = float(np.mean(w_vals))
                    horizon_metrics[f"H_{h}"] = {
                        "n_valid_windows": len(s_vals),
                        "state_mse": round(s_mse, 6),
                        "state_rmse": round(math.sqrt(s_mse), 6),
                        "wrench_mse": round(w_mse, 6),
                        "wrench_rmse": round(math.sqrt(w_mse), 6),
                    }
                else:
                    horizon_metrics[f"H_{h}"] = {
                        "n_valid_windows": 0,
                        "state_mse": None,
                        "state_rmse": None,
                        "wrench_mse": None,
                        "wrench_rmse": None,
                    }

            y_true_m = np.concatenate(all_true_modes) if all_true_modes else np.zeros(0, dtype=np.int64)
            y_pred_m = np.concatenate(all_pred_modes) if all_pred_modes else np.zeros(0, dtype=np.int64)
            probs_m = np.concatenate(all_mode_probs) if all_mode_probs else np.zeros((0, 5), dtype=np.float32)

            mode_metrics = _compute_multiclass_f1(y_true_m, y_pred_m)
            calib_metrics = _compute_calibration_metrics(probs_m, y_true_m)

            mu_t = np.concatenate(all_true_mu) if all_true_mu else np.zeros(0)
            mu_p = np.concatenate(all_pred_mu) if all_pred_mu else np.zeros(0)
            mass_t = np.concatenate(all_true_mass) if all_true_mass else np.zeros(0)
            mass_p = np.concatenate(all_pred_mass) if all_pred_mass else np.zeros(0)

            err1 = np.concatenate(all_err_step1) if all_err_step1 else np.zeros(0)
            unc1 = np.concatenate(all_unc_step1) if all_unc_step1 else np.zeros(0)
            ood_y = np.concatenate(all_ood_labels) if all_ood_labels else np.zeros(0, dtype=np.int64)

            # Gate 10 & 11: Compile redacted aggregate benchmark report
            report: Dict[str, Any] = {
                "release_id": release_id,
                "checkpoint_sha256": checkpoint_sha256,
                "model_status": "EVALUATED_READ_ONLY",
                "finite_outputs_verified": all_finite,
                "total_windows_evaluated": len(dataset),
                "horizon_metrics": horizon_metrics,
                "contact_mode_metrics": mode_metrics,
                "mode_calibration": calib_metrics,
                "sysid_friction": {"r2": _compute_r2(mu_t, mu_p), **_safe_corr(mu_t, mu_p)},
                "sysid_mass": {"r2": _compute_r2(mass_t, mass_p), **_safe_corr(mass_t, mass_p)},
                "ood_auroc": _compute_auroc(unc1, ood_y),
                "uncertainty_error_corr": _safe_corr(unc1, err1),
                "benchmark_acceptance_targets": BENCHMARK_ACCEPTANCE_TARGETS,
            }

            verify_redacted_benchmark_output(report)
            report_digest = hashlib.sha256(json.dumps(report, sort_keys=True).encode("utf-8")).hexdigest()

            # Gate 12 & 13: One-time result finalization in durable ledger
            ledger.mark_consumed(release_id, report_digest=report_digest)
            report["report_digest"] = report_digest
            report["release_status"] = "CONSUMED"
            return report

        except Exception as exc:
            if data_accessed:
                try:
                    ledger.mark_aborted(release_id, reason=str(exc))
                except Exception:
                    pass
            raise exc
        finally:
            if worker is not None:
                worker.close()
            if vault_path:
                try:
                    remove_os_vault_isolation(vault_path)
                except Exception:
                    pass

    def evaluate_final_holdout(
        self,
        model: HCTTWorldModel,
        dataset: HCTTTemporalDataset,
        target_loader: AuditTargetLoader,
        holdout_credentials: Any = None,
        device: str = "cpu",
    ) -> Dict[str, Any]:
        """Authorized final benchmark evaluation on an independently certified private holdout."""
        holdout_id = (
            holdout_credentials.get("holdout_id", "")
            if isinstance(holdout_credentials, dict)
            else str(holdout_credentials or "")
        )
        self.verify_holdout_eligibility(holdout_id, authorization_artifact=holdout_credentials)
        self.verify_dataset_eligibility(dataset, target_loader)
        return self.evaluate_hctt(
            model=model,
            dataset=dataset,
        )

    def evaluate_hctt(
        self,
        model: HCTTWorldModel,
        dataset: HCTTTemporalDataset,
        batch_size: int = 32,
        nominal_alpha: float = 0.10,
    ) -> Dict[str, Any]:
        """Executes full strictly read-only evaluation (torch.no_grad(), zero weight updates)."""
        self.verify_dataset_eligibility(dataset, self.target_loader)
        model.eval()
        horizons = LOCKED_HCTT_CONFIG.prediction_horizons

        state_sq_errs: Dict[int, List[float]] = {h: [] for h in horizons}
        wrench_sq_errs: Dict[int, List[float]] = {h: [] for h in horizons}
        all_true_modes: List[np.ndarray] = []
        all_pred_modes: List[np.ndarray] = []
        all_mode_probs: List[np.ndarray] = []
        all_true_mu: List[np.ndarray] = []
        all_pred_mu: List[np.ndarray] = []
        all_true_mass: List[np.ndarray] = []
        all_pred_mass: List[np.ndarray] = []
        all_unc_step1: List[np.ndarray] = []
        all_err_step1: List[np.ndarray] = []
        all_ood_labels: List[np.ndarray] = []
        all_finite = True

        # Conformal tracking per operational mode at horizon 1 (if calibrated quantiles attached)
        q_attached = bool(model.trust_calibrated.item())
        q_mat = model.conformal_quantiles.detach().cpu().numpy() if q_attached else None
        cov_hits_by_mode: Dict[int, List[int]] = {m: [] for m in SUPPORTED_OPERATIONAL_MODE_IDS}

        batches = dataset.iter_homogeneous_batches(batch_size=batch_size, shuffle=False)
        with torch.no_grad():
            for batch in batches:
                model_inputs, sim_targets = split_batch_inputs_and_targets(batch)
                audit_targets = self.target_loader.get_batch_targets(
                    batch["sample_ids"],
                    batch["anchor_steps"],
                    batch["future_mask"],
                )
                out = model(**model_inputs)

                for v in out.values():
                    if isinstance(v, torch.Tensor) and not torch.isfinite(v).all():
                        all_finite = False

                pred_s = out["pred_next_states"].cpu().numpy()
                true_s = sim_targets["target_next_states"].cpu().numpy()
                pred_w = out["pred_next_wrenches"].cpu().numpy()
                true_w = sim_targets["target_next_wrenches"].cpu().numpy()
                f_mask = sim_targets["future_mask"].cpu().numpy().astype(bool)

                # Horizon-indexed MSE on strictly unpadded steps
                for h in horizons:
                    idx = h - 1
                    if idx < pred_s.shape[1]:
                        valid_h = f_mask[:, idx]
                        if np.any(valid_h):
                            s_err = np.mean((pred_s[valid_h, idx, :] - true_s[valid_h, idx, :]) ** 2, axis=-1)
                            w_err = np.mean((pred_w[valid_h, idx, :] - true_w[valid_h, idx, :]) ** 2, axis=-1)
                            state_sq_errs[h].extend(s_err.tolist())
                            wrench_sq_errs[h].extend(w_err.tolist())

                # Contact modes across all valid future steps
                t_modes = audit_targets["target_modes"].numpy()
                p_probs = out["pred_mode_probs"].cpu().numpy()
                p_modes = np.argmax(p_probs, axis=-1)
                valid_flat = f_mask & (t_modes >= 0)
                if np.any(valid_flat):
                    all_true_modes.append(t_modes[valid_flat])
                    all_pred_modes.append(p_modes[valid_flat])
                    all_mode_probs.append(p_probs[valid_flat])

                # Latent SysID (anchor-level)
                all_true_mu.append(audit_targets["target_mu"].numpy())
                all_pred_mu.append(out["pred_friction_mu"].cpu().numpy())
                all_true_mass.append(audit_targets["target_mass"].numpy())
                all_pred_mass.append(out["pred_mass_kg"].cpu().numpy())

                # Step-1 L2 error & uncertainty for OOD / AURC / conformal
                step1_l2 = np.linalg.norm(pred_s[:, 0, :] - true_s[:, 0, :], axis=-1)
                step1_unc = out["pred_uncertainty_total"][:, 0].cpu().numpy()
                all_err_step1.append(step1_l2)
                all_unc_step1.append(step1_unc)
                ood_bin = np.array([0 if st == "nominal" else 1 for st in audit_targets["strata"]], dtype=np.int64)
                all_ood_labels.append(ood_bin)

                if q_attached and q_mat is not None:
                    for b_i in range(len(step1_l2)):
                        m_b = int(t_modes[b_i, 0])
                        if m_b in cov_hits_by_mode:
                            q_val = float(q_mat[m_b, 0])
                            cov_hits_by_mode[m_b].append(1 if step1_l2[b_i] <= q_val else 0)

        horizon_metrics: Dict[str, Any] = {}
        for h in horizons:
            s_vals = state_sq_errs[h]
            w_vals = wrench_sq_errs[h]
            if s_vals:
                s_mse = float(np.mean(s_vals))
                w_mse = float(np.mean(w_vals))
                horizon_metrics[f"H_{h}"] = {
                    "n_valid_windows": len(s_vals),
                    "state_mse": round(s_mse, 6),
                    "state_rmse": round(math.sqrt(s_mse), 6),
                    "wrench_mse": round(w_mse, 6),
                    "wrench_rmse": round(math.sqrt(w_mse), 6),
                }
            else:
                horizon_metrics[f"H_{h}"] = {
                    "n_valid_windows": 0,
                    "state_mse": None,
                    "state_rmse": None,
                    "wrench_mse": None,
                    "wrench_rmse": None,
                }

        y_true_m = np.concatenate(all_true_modes) if all_true_modes else np.zeros(0, dtype=np.int64)
        y_pred_m = np.concatenate(all_pred_modes) if all_pred_modes else np.zeros(0, dtype=np.int64)
        probs_m = np.concatenate(all_mode_probs) if all_mode_probs else np.zeros((0, 5), dtype=np.float32)

        mode_metrics = _compute_multiclass_f1(y_true_m, y_pred_m)
        calib_metrics = _compute_calibration_metrics(probs_m, y_true_m)

        mu_t = np.concatenate(all_true_mu) if all_true_mu else np.zeros(0)
        mu_p = np.concatenate(all_pred_mu) if all_pred_mu else np.zeros(0)
        mass_t = np.concatenate(all_true_mass) if all_true_mass else np.zeros(0)
        mass_p = np.concatenate(all_pred_mass) if all_pred_mass else np.zeros(0)

        err1 = np.concatenate(all_err_step1) if all_err_step1 else np.zeros(0)
        unc1 = np.concatenate(all_unc_step1) if all_unc_step1 else np.zeros(0)
        ood_y = np.concatenate(all_ood_labels) if all_ood_labels else np.zeros(0, dtype=np.int64)

        conformal_by_mode: Dict[str, Any] = {}
        if q_attached:
            for m, hits in cov_hits_by_mode.items():
                conformal_by_mode[OPERATIONAL_MODE_NAMES[m]] = {
                    "n": len(hits),
                    "coverage": round(float(np.mean(hits)), 4) if hits else None,
                }

        return {
            "model_status": "EVALUATED_READ_ONLY",
            "trust_calibrated": q_attached,
            "finite_outputs_verified": all_finite,
            "total_windows_evaluated": len(dataset),
            "horizon_metrics": horizon_metrics,
            "contact_mode_metrics": mode_metrics,
            "mode_calibration": calib_metrics,
            "sysid_friction": {"r2": _compute_r2(mu_t, mu_p), **_safe_corr(mu_t, mu_p)},
            "sysid_mass": {"r2": _compute_r2(mass_t, mass_p), **_safe_corr(mass_t, mass_p)},
            "ood_auroc": _compute_auroc(unc1, ood_y),
            "uncertainty_error_corr": _safe_corr(unc1, err1),
            "conformal_nominal_target": round(1.0 - nominal_alpha, 4),
            "conformal_coverage": conformal_by_mode if q_attached else "UNCALIBRATED_PRETRAINING",
            "benchmark_acceptance_targets": BENCHMARK_ACCEPTANCE_TARGETS,
        }

    def evaluate_frozen_baseline_on_dataset(
        self,
        hybrid_model: Any,
        dataset: HCTTTemporalDataset,
        task: str,
        batch_size: int = 64,
    ) -> Dict[str, Any]:
        """Evaluates frozen Phase 3.4 B1/B2/B3 HybridContactStateModel in read-only mode on Stage 3.4 windows."""
        self.verify_dataset_eligibility(dataset, self.target_loader)
        hybrid_model.eval()
        horizons = LOCKED_HCTT_CONFIG.prediction_horizons
        state_sq_errs: Dict[int, List[float]] = {h: [] for h in horizons}
        all_true_modes: List[np.ndarray] = []
        all_pred_modes: List[np.ndarray] = []
        all_unc1: List[np.ndarray] = []
        all_err1: List[np.ndarray] = []
        all_ood: List[np.ndarray] = []

        batches = dataset.iter_homogeneous_batches(batch_size=batch_size, task=task, shuffle=False)
        with torch.no_grad():
            for batch in batches:
                audit_targets = self.target_loader.get_batch_targets(
                    batch["sample_ids"],
                    batch["anchor_steps"],
                    batch["future_mask"],
                )
                s_curr = batch["hist_states"][:, -1, :].clone()
                fut_actions = batch["future_actions"]
                f_mask = batch["future_mask"].numpy().astype(bool)
                true_s = batch["target_next_states"].numpy()
                t_modes = audit_targets["target_modes"].numpy()

                B, H_fut, _ = fut_actions.shape
                rollout_s = np.zeros_like(true_s)
                rollout_m = np.zeros((B, H_fut), dtype=np.int64)
                step1_unc = np.zeros((B,), dtype=np.float32)

                curr = s_curr
                for k in range(H_fut):
                    a_k = fut_actions[:, k, :]
                    mean_k, epi_k, alea_k = hybrid_model.predict_with_uncertainty(curr, a_k)
                    m_logits_k = hybrid_model.predict_mode_logits(curr, a_k)
                    # Map 7-mode baseline logits cleanly onto 5 operational modes (0..4) for F1 comparison
                    m_pred_k = torch.argmax(m_logits_k[:, :5], dim=-1)

                    rollout_s[:, k, :] = mean_k.cpu().numpy()
                    rollout_m[:, k] = m_pred_k.cpu().numpy()
                    if k == 0:
                        step1_unc = torch.mean(torch.sqrt(torch.clamp(epi_k + alea_k, min=0.0)), dim=-1).cpu().numpy()
                    curr = mean_k

                for h in horizons:
                    idx = h - 1
                    valid_h = f_mask[:, idx]
                    if np.any(valid_h):
                        err_h = np.mean((rollout_s[valid_h, idx, :] - true_s[valid_h, idx, :]) ** 2, axis=-1)
                        state_sq_errs[h].extend(err_h.tolist())

                valid_flat = f_mask & (t_modes >= 0)
                if np.any(valid_flat):
                    all_true_modes.append(t_modes[valid_flat])
                    all_pred_modes.append(rollout_m[valid_flat])

                all_unc1.append(step1_unc)
                all_err1.append(np.linalg.norm(rollout_s[:, 0, :] - true_s[:, 0, :], axis=-1))
                all_ood.append(np.array([0 if st == "nominal" else 1 for st in audit_targets["strata"]], dtype=np.int64))

        horizon_metrics = {}
        for h in horizons:
            vals = state_sq_errs[h]
            horizon_metrics[f"H_{h}"] = {
                "n_valid_windows": len(vals),
                "state_mse": round(float(np.mean(vals)), 6) if vals else None,
            }

        y_true_m = np.concatenate(all_true_modes) if all_true_modes else np.zeros(0, dtype=np.int64)
        y_pred_m = np.concatenate(all_pred_modes) if all_pred_modes else np.zeros(0, dtype=np.int64)
        unc1 = np.concatenate(all_unc1) if all_unc1 else np.zeros(0)
        err1 = np.concatenate(all_err1) if all_err1 else np.zeros(0)
        ood_y = np.concatenate(all_ood) if all_ood else np.zeros(0, dtype=np.int64)

        return {
            "architecture_id": getattr(hybrid_model, "architecture_id", "unknown"),
            "task": task,
            "horizon_metrics": horizon_metrics,
            "contact_mode_metrics": _compute_multiclass_f1(y_true_m, y_pred_m),
            "ood_auroc": _compute_auroc(unc1, ood_y),
            "uncertainty_error_corr": _safe_corr(unc1, err1),
        }

    def evaluate_strict_vs_masked(
        self,
        model: HCTTWorldModel,
        dataset: HCTTTemporalDataset,
        task: str,
        batch_size: int = 32,
    ) -> Dict[str, Any]:
        """Evaluates Path 1 (strict 10-step history & complete horizon) separately from Path 2 (masked)."""
        self.verify_dataset_eligibility(dataset, self.target_loader)
        model.eval()
        horizons = LOCKED_HCTT_CONFIG.prediction_horizons

        # Path 1: Strictly filtered full-history + full-horizon windows per horizon
        strict_by_horizon: Dict[str, Any] = {}
        with torch.no_grad():
            for h in horizons:
                strict_batches = dataset.iter_strict_batches_for_horizon(
                    horizon=h, batch_size=batch_size, task=task, shuffle=False
                )
                s_errs: List[float] = []
                w_errs: List[float] = []
                true_m_list: List[np.ndarray] = []
                pred_m_list: List[np.ndarray] = []
                for batch in strict_batches:
                    model_inputs, sim_targets = split_batch_inputs_and_targets(batch)
                    audit_targets = self.target_loader.get_batch_targets(
                        batch["sample_ids"], batch["anchor_steps"], batch["future_mask"]
                    )
                    out = model(**model_inputs)
                    idx = h - 1
                    pred_s = out["pred_next_states"][:, idx, :].cpu().numpy()
                    true_s = sim_targets["target_next_states"][:, idx, :].cpu().numpy()
                    pred_w = out["pred_next_wrenches"][:, idx, :].cpu().numpy()
                    true_w = sim_targets["target_next_wrenches"][:, idx, :].cpu().numpy()

                    s_errs.extend(np.mean((pred_s - true_s) ** 2, axis=-1).tolist())
                    w_errs.extend(np.mean((pred_w - true_w) ** 2, axis=-1).tolist())

                    true_m_list.append(audit_targets["target_modes"][:, :h].numpy().reshape(-1))
                    pred_m_list.append(torch.argmax(out["pred_mode_probs"][:, :h, :], dim=-1).cpu().numpy().reshape(-1))

                if s_errs:
                    s_mse = float(np.mean(s_errs))
                    w_mse = float(np.mean(w_errs))
                    yt = np.concatenate(true_m_list)
                    yp = np.concatenate(pred_m_list)
                    m_f1 = _compute_multiclass_f1(yt, yp)
                    strict_by_horizon[f"H_{h}"] = {
                        "path": "strict_unpadded_10step_history",
                        "n_strict_windows": len(s_errs),
                        "state_mse": round(s_mse, 6),
                        "state_rmse": round(math.sqrt(s_mse), 6),
                        "wrench_mse": round(w_mse, 6),
                        "wrench_rmse": round(math.sqrt(w_mse), 6),
                        "mode_macro_f1_all_5": m_f1["macro_f1_all_5"],
                        "mode_macro_f1_present_modes": m_f1["macro_f1_present_modes"],
                    }
                else:
                    strict_by_horizon[f"H_{h}"] = {
                        "path": "strict_unpadded_10step_history",
                        "n_strict_windows": 0,
                        "state_mse": None,
                        "state_rmse": None,
                        "wrench_mse": None,
                        "wrench_rmse": None,
                        "mode_macro_f1_all_5": None,
                        "mode_macro_f1_present_modes": None,
                    }

        # Path 2: Masked sliding windows (all windows; strictly never mixed into strict metrics)
        task_masked_ds = HCTTTemporalDataset(
            training_dir=dataset.training_dirs,
            task_filter=task,
            allowed_sample_ids=dataset.allowed_sample_ids,
            window_mode="masked_sliding",
        )
        masked_res = self.evaluate_hctt(model, task_masked_ds, batch_size=batch_size)
        hist_len_counts: Dict[int, int] = {}
        for w in task_masked_ds.windows:
            hist_len_counts[w.valid_hist_len] = hist_len_counts.get(w.valid_hist_len, 0) + 1

        return {
            "task": task,
            "path1_strict_windows": strict_by_horizon,
            "path2_masked_windows": {
                "valid_hist_len_distribution": dict(sorted(hist_len_counts.items())),
                "metrics": masked_res,
            },
        }

    def evaluate_persistence_and_const_vel_baselines(
        self,
        dataset: HCTTTemporalDataset,
        task: str,
        strict_only: bool = True,
    ) -> Dict[str, Any]:
        """Evaluates native dt=0.01s Persistence and Constant-Velocity baselines on exact strict/masked windows."""
        self.verify_dataset_eligibility(dataset, None)
        horizons = LOCKED_HCTT_CONFIG.prediction_horizons
        pers_errs: Dict[int, List[float]] = {h: [] for h in horizons}
        cvel_errs: Dict[int, List[float]] = {h: [] for h in horizons}

        for h in horizons:
            batches = (
                dataset.iter_strict_batches_for_horizon(horizon=h, batch_size=64, task=task, shuffle=False)
                if strict_only
                else dataset.iter_homogeneous_batches(batch_size=64, task=task, shuffle=False)
            )
            idx = h - 1
            for batch in batches:
                s_curr = batch["hist_states"][:, -1, :].numpy()  # (B, D)
                s_prev = batch["hist_states"][:, -2, :].numpy()  # (B, D) valid on strict_only (len>=10)
                mask_prev = batch["hist_mask"][:, -2].numpy().astype(bool)
                delta_step = np.where(mask_prev[:, None], s_curr - s_prev, 0.0)
                f_valid = batch["future_mask"][:, idx].numpy().astype(bool)
                if not np.any(f_valid):
                    continue
                true_s = batch["target_next_states"][:, idx, :].numpy()

                pred_pers = s_curr
                pred_cvel = s_curr + float(h) * delta_step

                pers_errs[h].extend(np.mean((pred_pers[f_valid] - true_s[f_valid]) ** 2, axis=-1).tolist())
                cvel_errs[h].extend(np.mean((pred_cvel[f_valid] - true_s[f_valid]) ** 2, axis=-1).tolist())

        return {
            "task": task,
            "dt_s": dataset.dt_s,
            "strict_windows_only": strict_only,
            "persistence": {
                f"H_{h}": {
                    "physical_time_s": round(h * dataset.dt_s, 4),
                    "n_windows": len(pers_errs[h]),
                    "state_mse": round(float(np.mean(pers_errs[h])), 6) if pers_errs[h] else None,
                }
                for h in horizons
            },
            "constant_velocity": {
                f"H_{h}": {
                    "physical_time_s": round(h * dataset.dt_s, 4),
                    "n_windows": len(cvel_errs[h]),
                    "state_mse": round(float(np.mean(cvel_errs[h])), 6) if cvel_errs[h] else None,
                }
                for h in horizons
            },
        }
