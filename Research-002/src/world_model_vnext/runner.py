"""Training runner and governance authorization barrier for HOROS World Model vNext (HCTT-256).

Enforces the Stage 3.4 hard lock:
    TRAINING_PERMITTED = 0
    EXECUTION_AUTHORIZED = 0

Strictly prohibits gradient descent, loss.backward(), optimizer steps, or any weight update.
Supports only read-only architecture inspection, no-grad forward/loss verification,
and untrained checkpoint serialization/deserialization checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import torch

from .contracts import (
    COMPROMISED_HOLDOUT_ID,
    COMPROMISED_HOLDOUT_STATUS,
    CompromisedHoldoutError,
    ContractViolationError,
    HCTTArchitectureConfig,
    LOCKED_HCTT_CONFIG,
    is_compromised_holdout_sample_id,
)
from .hctt import HCTTWorldModel
from .losses import HCTTMultiObjectiveLoss


# Authoritative governance barrier constants — DO NOT MODIFY WITHOUT FORMAL AUTHORIZATION
TRAINING_PERMITTED: int = 0
EXECUTION_AUTHORIZED: int = 0


class TrainingAuthorizationError(ContractViolationError):
    """Raised whenever any training, backward pass, or parameter update is attempted while locked."""


class HCTTTrainingRunner:
    """Locked training orchestrator with fail-closed execution barrier."""

    def __init__(self, config: Optional[HCTTArchitectureConfig] = None, seed: int = 3400):
        self.config = config or LOCKED_HCTT_CONFIG
        self.seed = int(seed)
        self.training_permitted = TRAINING_PERMITTED
        self.execution_authorized = EXECUTION_AUTHORIZED
        self.training_steps_executed = 0
        self.parameter_updates_executed = 0

        torch.manual_seed(self.seed)
        self.model = HCTTWorldModel(self.config)
        self.loss_fn = HCTTMultiObjectiveLoss(self.config)

    def assert_training_locked(self) -> None:
        """Verifies barrier remains strictly enforced at zero updates."""
        if self.training_permitted != 0 or self.execution_authorized != 0:
            raise TrainingAuthorizationError("Governance constants tampered: authorization barrier breach.")
        if self.training_steps_executed != 0 or self.parameter_updates_executed != 0:
            raise TrainingAuthorizationError("Parameter update counter is non-zero under locked stage.")

    def _forbid_training(self, action_name: str) -> None:
        raise TrainingAuthorizationError(
            f"Operation '{action_name}' is STRICTLY PROHIBITED: "
            f"training_permitted={self.training_permitted}, execution_authorized={self.execution_authorized}. "
            "No backward passes, optimizer steps, or weight updates are authorized in Stage 3.4."
        )

    def train(self, *args: Any, **kwargs: Any) -> None:
        self._forbid_training("train")

    def run_training(self, *args: Any, **kwargs: Any) -> None:
        self._forbid_training("run_training")

    def run_epoch(self, *args: Any, **kwargs: Any) -> None:
        self._forbid_training("run_epoch")

    def step_optimizer(self, *args: Any, **kwargs: Any) -> None:
        self._forbid_training("step_optimizer")

    def backward(self, *args: Any, **kwargs: Any) -> None:
        self._forbid_training("backward")

    def verify_forward_and_loss_no_grad(
        self,
        model_inputs: Dict[str, Any],
        sim_targets: Dict[str, torch.Tensor],
        audit_targets: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Executes forward pass and multi-objective loss strictly under torch.no_grad()."""
        self.assert_training_locked()

        # Reject quarantined holdout data from forward/loss verification
        if "sample_id" in model_inputs:
            raw_sids = model_inputs["sample_id"]
            if isinstance(raw_sids, str):
                check_sids = [raw_sids]
            elif isinstance(raw_sids, (list, tuple, set)):
                check_sids = [str(s) for s in raw_sids]
            else:
                check_sids = []
            for sid in check_sids:
                if is_compromised_holdout_sample_id(sid):
                    raise CompromisedHoldoutError(
                        f"Sample ID '{sid}' belongs to quarantined holdout '{COMPROMISED_HOLDOUT_ID}' "
                        f"(status: {COMPROMISED_HOLDOUT_STATUS}). Consumption in runner is prohibited."
                    )
        if audit_targets and isinstance(audit_targets, dict):
            for sid in audit_targets.keys():
                if is_compromised_holdout_sample_id(str(sid)):
                    raise CompromisedHoldoutError(
                        f"Audit target '{sid}' belongs to quarantined holdout '{COMPROMISED_HOLDOUT_ID}' "
                        f"(status: {COMPROMISED_HOLDOUT_STATUS}). Consumption in runner is prohibited."
                    )

        self.model.eval()

        # Snapshot weight checksum before verification to prove zero parameter mutation
        before_norm = sum(float(p.detach().abs().sum().item()) for p in self.model.parameters())

        with torch.no_grad():
            outputs = self.model(**model_inputs)
            losses = self.loss_fn(outputs, sim_targets, audit_targets)

        after_norm = sum(float(p.detach().abs().sum().item()) for p in self.model.parameters())
        if abs(after_norm - before_norm) > 0.0:
            raise TrainingAuthorizationError("Parameters mutated during no-grad verification!")

        return {
            "forward_ok": True,
            "parameters_unchanged": True,
            "training_steps_executed": self.training_steps_executed,
            "loss_total": float(losses["loss_total"].item()),
            "l_sim_dynamics": float(losses["l_sim_dynamics"].item()),
            "l_state_mse": float(losses["l_state_mse"].item()),
            "l_wrench_mse": float(losses["l_wrench_mse"].item()),
            "l_mode_focal": float(losses["l_mode_focal"].item()),
            "l_sys_id": float(losses["l_sys_id"].item()),
            "l_distill": float(losses["l_distill"].item()),
            "distillation_active": bool(losses["distillation_active"]),
        }

    def save_untrained_checkpoint(self, path: Path | str) -> Path:
        """Serializes untrained model initialization strictly for checkpoint contract testing."""
        self.assert_training_locked()
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_family": self.config.model_family,
            "model_identifier": self.config.model_identifier,
            "architecture_config": self.config.__dict__,
            "model_state_dict": self.model.state_dict(),
            "training_permitted": self.training_permitted,
            "execution_authorized": self.execution_authorized,
            "training_performed": False,
            "training_steps": 0,
            "parameter_updates": 0,
            "seed": self.seed,
        }
        torch.save(payload, out_path)
        return out_path

    @classmethod
    def load_untrained_checkpoint(cls, path: Path | str) -> "HCTTTrainingRunner":
        """Loads an untrained checkpoint and verifies zero-training metadata."""
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if payload.get("training_performed", True) or int(payload.get("training_steps", -1)) != 0:
            raise TrainingAuthorizationError("Checkpoint violates Stage 3.4 untrained-only verification constraint.")
        cfg = HCTTArchitectureConfig(**payload["architecture_config"])
        runner = cls(config=cfg, seed=int(payload.get("seed", 3400)))
        runner.model.load_state_dict(payload["model_state_dict"])
        runner.model.eval()
        return runner
