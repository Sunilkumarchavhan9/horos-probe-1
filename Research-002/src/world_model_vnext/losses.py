"""Multi-objective loss formulation for HOROS World Model vNext (HCTT-256).

Implements the locked objective (HOROS-P1-R002-SPEC-LOCKED-001):
    L_total = 1.0 * L_sim_dynamics + 0.5 * L_mode_focal + 0.25 * L_sys_id + 0.10 * L_distill

Strict grounding & anti-fabrication rules:
- Simulator dynamics and mode focal losses are strictly masked by future_mask (zero contribution
  from right-padded beyond-episode timesteps).
- Teacher distillation loss (L_distill) is computed ONLY when genuine teacher hidden representations
  are provided; otherwise it evaluates to 0.0 with distillation_active=False (never fabricated).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .contracts import ContractViolationError, HCTTArchitectureConfig, LOCKED_HCTT_CONFIG


class HCTTMultiObjectiveLoss(nn.Module):
    """Locked multi-objective loss module for HOROS World Model vNext."""

    def __init__(self, config: Optional[HCTTArchitectureConfig] = None):
        super().__init__()
        self.config = config or LOCKED_HCTT_CONFIG

    def compute_dynamics_loss(
        self,
        pred_states: torch.Tensor,
        pred_state_logvar: torch.Tensor,
        target_states: torch.Tensor,
        pred_wrenches: torch.Tensor,
        pred_wrench_logvar: torch.Tensor,
        target_wrenches: torch.Tensor,
        future_mask: torch.Tensor,
    ) -> Dict[str, torch.Tensor]:
        """Masked simulator-grounded state and wrench prediction loss."""
        mask = future_mask.to(dtype=pred_states.dtype).unsqueeze(-1)  # (B, H_fut, 1)
        valid_steps = torch.clamp(mask.sum(), min=1.0)

        # State MSE + Smooth L1 + heteroscedastic Gaussian NLL
        state_sq_err = (pred_states - target_states) ** 2
        state_var = torch.exp(pred_state_logvar) + 1e-6
        state_nll = 0.5 * (state_sq_err / state_var + pred_state_logvar)
        l_state = (state_nll * mask).sum() / (valid_steps * pred_states.shape[-1])
        l_state_mse = (state_sq_err * mask).sum() / (valid_steps * pred_states.shape[-1])

        # Wrench MSE + Gaussian NLL
        wrench_sq_err = (pred_wrenches - target_wrenches) ** 2
        wrench_var = torch.exp(pred_wrench_logvar) + 1e-6
        wrench_nll = 0.5 * (wrench_sq_err / wrench_var + pred_wrench_logvar)
        l_wrench = (wrench_nll * mask).sum() / (valid_steps * pred_wrenches.shape[-1])
        l_wrench_mse = (wrench_sq_err * mask).sum() / (valid_steps * pred_wrenches.shape[-1])

        # Combine NLL-regularized terms with direct physical MSE anchor
        l_sim = 0.5 * (l_state + l_wrench) + (l_state_mse + l_wrench_mse)
        return {
            "l_sim_dynamics": l_sim,
            "l_state_mse": l_state_mse,
            "l_wrench_mse": l_wrench_mse,
        }

    def compute_mode_focal_loss(
        self,
        pred_mode_logits: torch.Tensor,
        target_modes: torch.Tensor,
        future_mask: torch.Tensor,
    ) -> torch.Tensor:
        """Masked 5-class focal cross-entropy over operational contact modes (0..4)."""
        B, H_fut, M = pred_mode_logits.shape
        valid_mask = future_mask.bool() & (target_modes >= 0)

        if not valid_mask.any():
            return torch.zeros((), dtype=pred_mode_logits.dtype, device=pred_mode_logits.device)

        flat_logits = pred_mode_logits[valid_mask]  # (N_valid, M)
        flat_targets = target_modes[valid_mask].long()

        if (flat_targets >= M).any() or (flat_targets < 0).any():
            bad_vals = sorted(set(int(v) for v in flat_targets.unique().tolist()))
            raise ContractViolationError(
                f"target_modes contains IDs outside [0, {M - 1}]: {bad_vals}"
            )

        log_probs = F.log_softmax(flat_logits, dim=-1)
        probs = torch.exp(log_probs)
        target_log_p = log_probs.gather(dim=-1, index=flat_targets.unsqueeze(-1)).squeeze(-1)
        target_p = probs.gather(dim=-1, index=flat_targets.unsqueeze(-1)).squeeze(-1)

        gamma = self.config.focal_gamma
        focal_weight = (1.0 - target_p).pow(gamma)
        loss = -(focal_weight * target_log_p).mean()
        return loss

    def compute_sys_id_loss(
        self,
        pred_mu: torch.Tensor,
        target_mu: torch.Tensor,
        pred_mass: torch.Tensor,
        target_mass: torch.Tensor,
    ) -> torch.Tensor:
        """Auxiliary supervised regression loss on latent friction and object mass."""
        l_mu = F.smooth_l1_loss(pred_mu, target_mu.to(dtype=pred_mu.dtype), beta=0.1)
        l_mass = F.smooth_l1_loss(pred_mass, target_mass.to(dtype=pred_mass.dtype), beta=0.1)
        return l_mu + l_mass

    def compute_distillation_loss(
        self,
        student_repr: torch.Tensor,
        teacher_representations: Optional[torch.Tensor],
    ) -> Tuple[torch.Tensor, bool]:
        """Cosine alignment loss strictly gated on real teacher representation availability."""
        if teacher_representations is None:
            zero = torch.zeros((), dtype=student_repr.dtype, device=student_repr.device)
            return zero, False

        t_repr = teacher_representations.to(dtype=student_repr.dtype, device=student_repr.device)
        if t_repr.shape != student_repr.shape:
            raise ContractViolationError(
                f"Teacher representation shape {tuple(t_repr.shape)} != student {tuple(student_repr.shape)}"
            )
        if not torch.isfinite(t_repr).all():
            raise ContractViolationError("Non-finite teacher_representations supplied.")

        cos_sim = F.cosine_similarity(student_repr, t_repr, dim=-1)
        return (1.0 - cos_sim).mean(), True

    def forward(
        self,
        outputs: Dict[str, torch.Tensor],
        sim_targets: Dict[str, torch.Tensor],
        audit_targets: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Computes all objective components and locked weighted sum."""
        future_mask = sim_targets["future_mask"]

        dyn = self.compute_dynamics_loss(
            pred_states=outputs["pred_next_states"],
            pred_state_logvar=outputs["pred_state_logvar"],
            target_states=sim_targets["target_next_states"],
            pred_wrenches=outputs["pred_next_wrenches"],
            pred_wrench_logvar=outputs["pred_wrench_logvar"],
            target_wrenches=sim_targets["target_next_wrenches"],
            future_mask=future_mask,
        )
        l_mode = self.compute_mode_focal_loss(
            pred_mode_logits=outputs["pred_mode_logits"],
            target_modes=audit_targets["target_modes"].to(device=outputs["pred_mode_logits"].device),
            future_mask=future_mask,
        )
        l_sysid = self.compute_sys_id_loss(
            pred_mu=outputs["pred_friction_mu"],
            target_mu=audit_targets["target_mu"].to(device=outputs["pred_friction_mu"].device),
            pred_mass=outputs["pred_mass_kg"],
            target_mass=audit_targets["target_mass"].to(device=outputs["pred_mass_kg"].device),
        )
        l_distill, distill_active = self.compute_distillation_loss(
            student_repr=outputs["latent_history_repr"],
            teacher_representations=audit_targets.get("teacher_representations"),
        )

        l_total = (
            self.config.weight_sim_dynamics * dyn["l_sim_dynamics"]
            + self.config.weight_mode_focal * l_mode
            + self.config.weight_sys_id * l_sysid
            + self.config.weight_distill * l_distill
        )

        if not torch.isfinite(l_total):
            raise ContractViolationError("Non-finite total loss detected.")

        return {
            "loss_total": l_total,
            "l_sim_dynamics": dyn["l_sim_dynamics"],
            "l_state_mse": dyn["l_state_mse"],
            "l_wrench_mse": dyn["l_wrench_mse"],
            "l_mode_focal": l_mode,
            "l_sys_id": l_sysid,
            "l_distill": l_distill,
            "distillation_active": distill_active,
        }
