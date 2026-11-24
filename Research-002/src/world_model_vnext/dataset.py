"""Temporal sliding-window dataset pipeline for HOROS World Model vNext (HCTT-256).

Strictly loads only model-visible NPZ archives from experiments/stage3_production/training/:
- Never accesses privileged audit files, seeds, rewards, friction, mass, or ground-truth modes.
- Constructs causal 10-step historical observations and up to 20-step future candidate actions.
- Enforces past-action causality: history step tau receives a_{tau-1} (with a_{-1}=0),
  never future actions a_{t:t+H-1} or future states s_{t+1:t+H}.
- Applies explicit padding masks (hist_mask, future_mask) rather than silent zero padding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import torch

from .contracts import (
    ContractViolationError,
    LOCKED_HCTT_CONFIG,
    MODEL_VISIBLE_NPZ_KEYS,
    TASK_INTERFACES,
    assert_no_privileged_keys_in_inputs,
    get_task_interface,
)


@dataclass(frozen=True)
class WindowSampleIndex:
    """Identifies one temporal anchor window inside an episode archive."""

    episode_idx: int
    sample_id: str
    task: str
    anchor_step: int
    valid_hist_len: int
    valid_future_len: int


@dataclass
class EpisodeArrayRecord:
    """In-memory non-privileged trajectory arrays loaded from one training .npz."""

    sample_id: str
    task: str
    states: np.ndarray
    actions: np.ndarray
    next_states: np.ndarray
    wrenches: np.ndarray
    next_wrenches: np.ndarray
    episode_length: int


def _refuse_colocated_audit_files(training_dir: Path) -> None:
    if not training_dir.is_dir():
        raise ContractViolationError(f"Training directory does not exist: {training_dir}")
    for entry in training_dir.iterdir():
        name = entry.name.lower()
        if (
            name.endswith(".privileged.json")
            or name.endswith(".rejected.json")
            or "audit" in name
            or "seed_registry" in name
            or "accepted_manifest" in name
        ):
            raise ContractViolationError(
                f"Training directory contains prohibited audit/registry artifact: {entry.name}"
            )


def _scalar_str(val: Any) -> str:
    arr = np.asarray(val)
    if arr.shape == ():
        return str(arr.item())
    return str(arr.reshape(-1)[0])


def load_clean_training_episode(npz_path: Path) -> EpisodeArrayRecord:
    """Loads and validates one model-visible .npz archive without modifying it."""
    with np.load(npz_path, allow_pickle=False) as data:
        keys = set(data.files)
        if keys != set(MODEL_VISIBLE_NPZ_KEYS):
            raise ContractViolationError(
                f"Archive {npz_path.name} key mismatch. Expected {sorted(MODEL_VISIBLE_NPZ_KEYS)}, got {sorted(keys)}"
            )
        task = _scalar_str(data["task"])
        sample_id = _scalar_str(data["sample_id"])
        spec = get_task_interface(task)

        states = np.asarray(data["states"], dtype=np.float32)
        actions = np.asarray(data["actions"], dtype=np.float32)
        next_states = np.asarray(data["next_states"], dtype=np.float32)
        wrenches = np.asarray(data["wrenches"], dtype=np.float32)
        next_wrenches = np.asarray(data["next_wrenches"], dtype=np.float32)

    T = states.shape[0]
    if T != spec.canonical_episode_length:
        raise ContractViolationError(
            f"Episode {sample_id} ({task}) expected canonical length {spec.canonical_episode_length}, got {T}"
        )
    if states.shape != (T, spec.state_dim) or next_states.shape != (T, spec.state_dim):
        raise ContractViolationError(
            f"Episode {sample_id} ({task}) state shape invalid: {states.shape} / {next_states.shape}"
        )
    if actions.shape != (T, spec.action_dim):
        raise ContractViolationError(
            f"Episode {sample_id} ({task}) action shape invalid: {actions.shape}"
        )
    if wrenches.shape != (T, spec.wrench_dim) or next_wrenches.shape != (T, spec.wrench_dim):
        raise ContractViolationError(
            f"Episode {sample_id} ({task}) wrench shape invalid: {wrenches.shape} / {next_wrenches.shape}"
        )

    for arr_name, arr in (
        ("states", states),
        ("actions", actions),
        ("next_states", next_states),
        ("wrenches", wrenches),
        ("next_wrenches", next_wrenches),
    ):
        if not np.all(np.isfinite(arr)):
            raise ContractViolationError(f"Non-finite values detected in {arr_name} for {sample_id}")

    # Verify strict temporal transition continuity s_{t+1} == next_states[t]
    if T > 1:
        if not np.allclose(states[1:], next_states[:-1], atol=1e-5, rtol=0.0):
            raise ContractViolationError(f"Temporal state continuity broken in {sample_id}")
        if not np.allclose(wrenches[1:], next_wrenches[:-1], atol=1e-5, rtol=0.0):
            raise ContractViolationError(f"Temporal wrench continuity broken in {sample_id}")

    return EpisodeArrayRecord(
        sample_id=sample_id,
        task=task,
        states=states,
        actions=actions,
        next_states=next_states,
        wrenches=wrenches,
        next_wrenches=next_wrenches,
        episode_length=T,
    )


class HCTTTemporalDataset:
    """Temporal sliding-window dataset over Stage 3.3 model-visible NPZ archives."""

    def __init__(
        self,
        training_dir: Path | str,
        task_filter: Optional[str] = None,
        allowed_sample_ids: Optional[Iterable[str]] = None,
        history_horizon: int = LOCKED_HCTT_CONFIG.history_horizon,
        future_horizon: int = LOCKED_HCTT_CONFIG.max_future_horizon,
        window_mode: str = "masked_sliding",
        min_history_steps: int = 1,
        min_future_steps: int = 1,
    ):
        self.training_dir = Path(training_dir)
        _refuse_colocated_audit_files(self.training_dir)

        if task_filter is not None:
            get_task_interface(task_filter)
        if window_mode not in ("masked_sliding", "strict_unpadded"):
            raise ValueError(f"Unsupported window_mode '{window_mode}'.")

        self.task_filter = task_filter
        self.allowed_sample_ids: Optional[Set[str]] = set(allowed_sample_ids) if allowed_sample_ids is not None else None
        self.history_horizon = int(history_horizon)
        self.future_horizon = int(future_horizon)
        self.window_mode = window_mode
        self.min_history_steps = int(min_history_steps)
        self.min_future_steps = int(min_future_steps)
        self.dt_s = LOCKED_HCTT_CONFIG.dt_s

        self.episodes: List[EpisodeArrayRecord] = []
        self.sample_id_to_episode_idx: Dict[str, int] = {}
        self.windows: List[WindowSampleIndex] = []

        self._load_episodes()
        self._build_window_index()

    def _load_episodes(self) -> None:
        npz_paths = sorted(self.training_dir.glob("*.npz"))
        seen_ids: Set[str] = set()
        for path in npz_paths:
            rec = load_clean_training_episode(path)
            if rec.sample_id in seen_ids:
                raise ContractViolationError(f"Duplicate sample_id '{rec.sample_id}' in {self.training_dir}")
            seen_ids.add(rec.sample_id)

            if self.task_filter is not None and rec.task != self.task_filter:
                continue
            if self.allowed_sample_ids is not None and rec.sample_id not in self.allowed_sample_ids:
                continue

            ep_idx = len(self.episodes)
            self.episodes.append(rec)
            self.sample_id_to_episode_idx[rec.sample_id] = ep_idx

        if self.allowed_sample_ids is not None:
            missing = self.allowed_sample_ids - set(self.sample_id_to_episode_idx.keys())
            if missing and self.task_filter is None:
                raise ContractViolationError(
                    f"Split requested {len(missing)} sample_ids absent from {self.training_dir}: {sorted(missing)[:5]}"
                )

    def _build_window_index(self) -> None:
        self.windows = []
        for ep_idx, ep in enumerate(self.episodes):
            T = ep.episode_length
            req_hist = self.history_horizon if self.window_mode == "strict_unpadded" else self.min_history_steps
            req_fut = self.min_future_steps
            for t in range(T):
                valid_hist = min(self.history_horizon, t + 1)
                valid_fut = min(self.future_horizon, T - t)
                if valid_hist < req_hist or valid_fut < req_fut:
                    continue
                self.windows.append(
                    WindowSampleIndex(
                        episode_idx=ep_idx,
                        sample_id=ep.sample_id,
                        task=ep.task,
                        anchor_step=t,
                        valid_hist_len=valid_hist,
                        valid_future_len=valid_fut,
                    )
                )

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        w = self.windows[idx]
        ep = self.episodes[w.episode_idx]
        spec = get_task_interface(ep.task)
        t = w.anchor_step
        H_hist = self.history_horizon
        H_fut = self.future_horizon
        T = ep.episode_length

        # 1. History window [t - valid_hist + 1 .. t], right-aligned into (H_hist, D)
        hist_start = max(0, t - H_hist + 1)
        hist_end = t + 1
        n_hist = hist_end - hist_start
        pad_left = H_hist - n_hist

        hist_states = np.zeros((H_hist, spec.state_dim), dtype=np.float32)
        hist_prev_actions = np.zeros((H_hist, spec.action_dim), dtype=np.float32)
        hist_wrenches = np.zeros((H_hist, spec.wrench_dim), dtype=np.float32)
        hist_mask = np.zeros((H_hist,), dtype=bool)

        hist_states[pad_left:] = ep.states[hist_start:hist_end]
        hist_wrenches[pad_left:] = ep.wrenches[hist_start:hist_end]
        hist_mask[pad_left:] = True

        # Causal past-executed action: history index tau gets actions[tau - 1] (0 at tau == 0).
        # Crucially, actions[t] is NOT placed into hist_prev_actions; it belongs strictly to future_actions[0].
        for offset, tau in enumerate(range(hist_start, hist_end)):
            if tau > 0:
                hist_prev_actions[pad_left + offset] = ep.actions[tau - 1]

        # 2. Future candidate actions & targets [t .. min(T, t + H_fut))
        fut_end = min(T, t + H_fut)
        n_fut = fut_end - t

        future_actions = np.zeros((H_fut, spec.action_dim), dtype=np.float32)
        future_mask = np.zeros((H_fut,), dtype=bool)
        target_next_states = np.zeros((H_fut, spec.state_dim), dtype=np.float32)
        target_next_wrenches = np.zeros((H_fut, spec.wrench_dim), dtype=np.float32)

        future_actions[:n_fut] = ep.actions[t:fut_end]
        future_mask[:n_fut] = True
        target_next_states[:n_fut] = ep.next_states[t:fut_end]
        target_next_wrenches[:n_fut] = ep.next_wrenches[t:fut_end]

        return {
            "sample_id": ep.sample_id,
            "task": ep.task,
            "anchor_step": t,
            "valid_hist_len": n_hist,
            "valid_future_len": n_fut,
            "dt_s": self.dt_s,
            "hist_states": hist_states,
            "hist_prev_actions": hist_prev_actions,
            "hist_wrenches": hist_wrenches,
            "hist_mask": hist_mask,
            "future_actions": future_actions,
            "future_mask": future_mask,
            "target_next_states": target_next_states,
            "target_next_wrenches": target_next_wrenches,
        }

    def iter_homogeneous_batches(
        self,
        batch_size: int = 32,
        task: Optional[str] = None,
        shuffle: bool = False,
        seed: int = 42,
    ) -> List[Dict[str, Any]]:
        """Yields homogeneous-shape batches grouped strictly by task."""
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")

        target_tasks = [task] if task is not None else sorted(set(w.task for w in self.windows))
        rng = np.random.RandomState(seed) if shuffle else None
        batches: List[Dict[str, Any]] = []

        for t_name in target_tasks:
            indices = [i for i, w in enumerate(self.windows) if w.task == t_name]
            if not indices:
                continue
            if rng is not None:
                rng.shuffle(indices)

            for start in range(0, len(indices), batch_size):
                chunk_idx = indices[start : start + batch_size]
                items = [self[i] for i in chunk_idx]
                batches.append(collate_hctt_batch(items))

        return batches

    def feasibility_summary(self) -> Dict[str, Any]:
        """Documents exact window & horizon coverage per task under current dataset."""
        summary: Dict[str, Any] = {}
        for task_name in sorted(set(ep.task for ep in self.episodes)):
            ep_list = [ep for ep in self.episodes if ep.task == task_name]
            win_list = [w for w in self.windows if w.task == task_name]
            T = ep_list[0].episode_length if ep_list else 0

            horizon_valid_counts: Dict[int, Dict[str, int]] = {}
            for h in LOCKED_HCTT_CONFIG.prediction_horizons:
                masked_valid = sum(1 for w in win_list if w.valid_future_len >= h)
                strict_full = sum(
                    1 for w in win_list if w.valid_hist_len == self.history_horizon and w.valid_future_len >= h
                )
                horizon_valid_counts[h] = {
                    "windows_with_valid_horizon": masked_valid,
                    "windows_with_full_10step_history_and_horizon": strict_full,
                }

            summary[task_name] = {
                "num_episodes": len(ep_list),
                "episode_length": T,
                "total_windows": len(win_list),
                "horizons": horizon_valid_counts,
            }
        return summary


def collate_hctt_batch(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Collates homogeneous-task window dicts into batched PyTorch tensors."""
    if not items:
        raise ValueError("Cannot collate empty batch.")
    tasks = {it["task"] for it in items}
    if len(tasks) != 1:
        raise ContractViolationError(
            f"Mixed tasks in single batch are forbidden: {sorted(tasks)}. Batch homogeneously per task."
        )
    task = items[0]["task"]
    return {
        "task": task,
        "sample_ids": [it["sample_id"] for it in items],
        "anchor_steps": [int(it["anchor_step"]) for it in items],
        "valid_hist_lens": [int(it["valid_hist_len"]) for it in items],
        "valid_future_lens": [int(it["valid_future_len"]) for it in items],
        "dt_s": float(items[0]["dt_s"]),
        "hist_states": torch.from_numpy(np.stack([it["hist_states"] for it in items])),
        "hist_prev_actions": torch.from_numpy(np.stack([it["hist_prev_actions"] for it in items])),
        "hist_wrenches": torch.from_numpy(np.stack([it["hist_wrenches"] for it in items])),
        "hist_mask": torch.from_numpy(np.stack([it["hist_mask"] for it in items])),
        "future_actions": torch.from_numpy(np.stack([it["future_actions"] for it in items])),
        "future_mask": torch.from_numpy(np.stack([it["future_mask"] for it in items])),
        "target_next_states": torch.from_numpy(np.stack([it["target_next_states"] for it in items])),
        "target_next_wrenches": torch.from_numpy(np.stack([it["target_next_wrenches"] for it in items])),
    }


def split_batch_inputs_and_targets(batch: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, torch.Tensor]]:
    """Separates inference-safe model inputs from supervised next-state/wrench targets."""
    model_inputs: Dict[str, Any] = {
        "task": batch["task"],
        "hist_states": batch["hist_states"],
        "hist_prev_actions": batch["hist_prev_actions"],
        "hist_wrenches": batch["hist_wrenches"],
        "hist_mask": batch["hist_mask"],
        "future_actions": batch["future_actions"],
        "future_action_mask": batch["future_mask"],
    }
    assert_no_privileged_keys_in_inputs(model_inputs)

    sim_targets = {
        "target_next_states": batch["target_next_states"],
        "target_next_wrenches": batch["target_next_wrenches"],
        "future_mask": batch["future_mask"],
    }
    return model_inputs, sim_targets
