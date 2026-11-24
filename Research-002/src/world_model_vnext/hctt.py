"""HOROS World Model vNext — History-Conditioned Temporal Transformer (HCTT-256).

Implements the frozen Stage 3 architecture specification:
- 6 Transformer decoder layers, d_model=256, 8 attention heads, d_head=32
- 1D Rotary Position Embeddings (RoPE) on queries and keys
- Causal temporal attention over [10-step history tokens, up to 20-step future action tokens]
- Strictly separate task-specific input/output projections (Prelift 23/5, Peg-in-Hole 15/6)
- Non-privileged observable continuous contact signal extraction
- Model-own causal mode feedback (never ground-truth contact modes)
- Decoupled kinematics residual, 6-axis wrench, 5-class contact-mode, latent (mu, mass),
  and predictive uncertainty / trust score heads
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from .contracts import (
    ContractViolationError,
    HCTTArchitectureConfig,
    LOCKED_HCTT_CONFIG,
    TASK_INTERFACES,
    assert_no_privileged_keys_in_inputs,
    get_task_interface,
)


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    """Rotates half the hidden dims of the input for 1D RoPE."""
    half = x.shape[-1] // 2
    x1 = x[..., :half]
    x2 = x[..., half:]
    return torch.cat((-x2, x1), dim=-1)


class RotaryPositionEmbedding(nn.Module):
    """1D Rotary Position Embeddings (RoPE) for temporal self-attention."""

    def __init__(self, head_dim: int, max_seq_len: int = 64, theta: float = 10000.0):
        super().__init__()
        if head_dim % 2 != 0:
            raise ValueError(f"RoPE head_dim must be even, got {head_dim}")
        self.head_dim = head_dim
        self.max_seq_len = max_seq_len
        inv_freq = 1.0 / (theta ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)
        self._build_cache(max_seq_len)

    def _build_cache(self, seq_len: int) -> None:
        positions = torch.arange(seq_len, dtype=torch.float32, device=self.inv_freq.device)
        freqs = torch.outer(positions, self.inv_freq)  # (L, head_dim // 2)
        emb = torch.cat((freqs, freqs), dim=-1)        # (L, head_dim)
        self.register_buffer("cos_cached", emb.cos()[None, None, :, :], persistent=False)
        self.register_buffer("sin_cached", emb.sin()[None, None, :, :], persistent=False)

    def forward(self, q: torch.Tensor, k: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Applies RoPE to Q and K tensors of shape (B, n_heads, L, head_dim)."""
        seq_len = q.shape[2]
        if seq_len > self.cos_cached.shape[2]:
            self._build_cache(seq_len)
        cos = self.cos_cached[:, :, :seq_len, :].to(dtype=q.dtype)
        sin = self.sin_cached[:, :, :seq_len, :].to(dtype=q.dtype)
        q_rot = (q * cos) + (_rotate_half(q) * sin)
        k_rot = (k * cos) + (_rotate_half(k) * sin)
        return q_rot, k_rot


class CausalRoPESelfAttention(nn.Module):
    """Multi-head causal self-attention with RoPE and explicit padding/causal mask."""

    def __init__(self, d_model: int, num_heads: int, d_head: int, dropout: float, rope: RotaryPositionEmbedding):
        super().__init__()
        self.d_model = d_model
        self.num_heads = num_heads
        self.d_head = d_head
        self.rope = rope

        self.qkv_proj = nn.Linear(d_model, 3 * d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.attn_dropout = nn.Dropout(dropout)
        self.resid_dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        """Forward pass.

        Args:
            x: (B, L, d_model)
            attention_mask: boolean mask of shape (B, L, L) where True = allowed to attend.
        """
        B, L, D = x.shape
        qkv = self.qkv_proj(x).view(B, L, 3, self.num_heads, self.d_head)
        q, k, v = qkv.unbind(dim=2)  # each (B, L, H, d_head)
        q = q.transpose(1, 2)        # (B, H, L, d_head)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)

        q, k = self.rope(q, k)

        scale = 1.0 / math.sqrt(self.d_head)
        scores = torch.matmul(q, k.transpose(-2, -1)) * scale  # (B, H, L, L)
        mask = attention_mask.unsqueeze(1)  # (B, 1, L, L)
        scores = scores.masked_fill(~mask, -1e9)
        attn = F.softmax(scores, dim=-1)
        attn = self.attn_dropout(attn)

        out = torch.matmul(attn, v)  # (B, H, L, d_head)
        out = out.transpose(1, 2).contiguous().view(B, L, D)
        return self.resid_dropout(self.out_proj(out))


class HCTTDecoderLayer(nn.Module):
    """Pre-LayerNorm Transformer decoder block with RoPE causal self-attention."""

    def __init__(
        self,
        d_model: int,
        num_heads: int,
        d_head: int,
        ffn_mult: int,
        dropout: float,
        rope: RotaryPositionEmbedding,
    ):
        super().__init__()
        self.norm1 = nn.LayerNorm(d_model)
        self.attn = CausalRoPESelfAttention(d_model, num_heads, d_head, dropout, rope)
        self.norm2 = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_model * ffn_mult),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model * ffn_mult, d_model),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.norm1(x), attention_mask)
        x = x + self.ffn(self.norm2(x))
        return x


def extract_observable_contact_signals(
    task: str,
    states: torch.Tensor,
    wrenches: torch.Tensor,
) -> torch.Tensor:
    """Derives continuous non-privileged contact signals [||F_N||, s_pad, v_slip].

    Strictly accesses only model-visible state and 6-axis wrench channels.
    Zero privileged friction, mass, clearance, or simulator mode access.
    Returns shape (..., 3).
    """
    spec = get_task_interface(task)
    if states.shape[-1] != spec.state_dim:
        raise ContractViolationError(
            f"Task '{task}' requires state_dim={spec.state_dim}, got {states.shape[-1]}"
        )
    if wrenches.shape[-1] != spec.wrench_dim:
        raise ContractViolationError(
            f"Expected wrench_dim={spec.wrench_dim}, got {wrenches.shape[-1]}"
        )

    if task == "compliant_prelift":
        # [12:16] finger normal forces (N), [16:20] slip velocities (m/s), wrench[2] vertical shear (N)
        fn_total = torch.sum(torch.clamp(states[..., 12:16], min=0.0), dim=-1)
        fz_shear = torch.abs(wrenches[..., 2])
        s_pad = fz_shear / 6000.0  # total elastomeric pad stiffness 4 * 1500 N/m
        v_slip = torch.max(torch.abs(states[..., 16:20]), dim=-1).values
    else:
        # peg_in_hole 15-D layout: pos[0:3], vel[3:6], rot[6:9], wrench[9:15]
        # Note: external wrench tensor is (..., 6) [Fx, Fy, Fz, Tx, Ty, Tz]
        f_lat = torch.linalg.norm(wrenches[..., :2], dim=-1)
        f_ax = torch.abs(wrenches[..., 2])
        fn_total = torch.maximum(f_lat, f_ax)
        s_pad = f_lat / 5000.0  # nominal peg wall stiffness 5000 N/m
        v_slip = torch.linalg.norm(states[..., 3:6], dim=-1) * 1e-3  # mm/s -> m/s scale

    return torch.stack((fn_total, s_pad, v_slip), dim=-1)


def build_causal_history_future_mask(
    hist_mask: torch.Tensor,
    future_mask: torch.Tensor,
) -> torch.Tensor:
    """Constructs causal + padding mask of shape (B, L, L) where L = H_hist + H_fut.

    Invariants:
    1. Strictly causal: token i can only attend to keys j <= i.
    2. Invalid padded keys (where combined_valid[b, j] == False) are NEVER visible
       to any valid query token.
    3. Any invalid left-padded query token (combined_valid[b, i] == False) attends
       strictly to itself (diagonal = True) to keep softmax numerically finite without
       influencing any valid downstream token.
    """
    B, H_hist = hist_mask.shape
    H_fut = future_mask.shape[1]
    L = H_hist + H_fut
    device = hist_mask.device

    combined_valid = torch.cat((hist_mask.bool(), future_mask.bool()), dim=1)  # (B, L)
    causal = torch.tril(torch.ones((L, L), dtype=torch.bool, device=device))    # (L, L)

    # Valid keys visible causally: (B, L_q, L_k)
    attn_mask = causal.unsqueeze(0) & combined_valid.unsqueeze(1)

    # For padded query rows, allow self-attention on diagonal only to avoid softmax NaN
    eye = torch.eye(L, dtype=torch.bool, device=device).unsqueeze(0)
    padded_q = ~combined_valid.unsqueeze(2)  # (B, L_q, 1)
    attn_mask = torch.where(padded_q, eye, attn_mask)
    return attn_mask


class HCTTWorldModel(nn.Module):
    """HOROS World Model vNext — History-Conditioned Temporal Transformer (HCTT-256)."""

    def __init__(self, config: Optional[HCTTArchitectureConfig] = None):
        super().__init__()
        self.config = config or LOCKED_HCTT_CONFIG
        D = self.config.d_model
        H_hist = self.config.history_horizon
        H_fut = self.config.max_future_horizon
        M = self.config.num_operational_modes

        # Task context embeddings: 0=compliant_prelift, 1=peg_in_hole
        self.task_embedding = nn.Embedding(len(TASK_INTERFACES), D)

        # Task-specific non-privileged input projections (never mixed across tasks)
        self.state_in_proj = nn.ModuleDict({
            name: nn.Sequential(
                nn.Linear(spec.state_dim, D),
                nn.LayerNorm(D),
                nn.GELU(),
            )
            for name, spec in TASK_INTERFACES.items()
        })
        self.prev_action_in_proj = nn.ModuleDict({
            name: nn.Sequential(
                nn.Linear(spec.action_dim, D // 2),
                nn.GELU(),
            )
            for name, spec in TASK_INTERFACES.items()
        })
        self.future_action_in_proj = nn.ModuleDict({
            name: nn.Sequential(
                nn.Linear(spec.action_dim, D),
                nn.LayerNorm(D),
                nn.GELU(),
            )
            for name, spec in TASK_INTERFACES.items()
        })

        # Shared physical wrench + observable continuous signal + model-own mode prior projections
        self.wrench_in_proj = nn.Sequential(
            nn.Linear(6, D // 2),
            nn.GELU(),
        )
        self.contact_signal_proj = nn.Sequential(
            nn.Linear(3, D // 4),
            nn.GELU(),
        )
        self.mode_prob_in_proj = nn.Sequential(
            nn.Linear(M, D // 4),
            nn.GELU(),
        )

        # Token fusion: state(D) + prev_action(D/2) + wrench(D/2) + signal(D/4) + mode_prob(D/4) = 2.5*D
        fused_dim = D + (D // 2) + (D // 2) + (D // 4) + (D // 4)
        self.history_token_fusion = nn.Sequential(
            nn.Linear(fused_dim, D),
            nn.LayerNorm(D),
            nn.GELU(),
            nn.Linear(D, D),
        )
        self.future_horizon_embedding = nn.Embedding(H_fut, D)
        self.token_type_embedding = nn.Embedding(2, D)  # 0=history, 1=future_action

        # Transformer Decoder Backbone with 1D RoPE
        self.rope = RotaryPositionEmbedding(
            head_dim=self.config.d_head,
            max_seq_len=H_hist + H_fut + 8,
            theta=self.config.rope_theta,
        )
        self.layers = nn.ModuleList([
            HCTTDecoderLayer(
                d_model=D,
                num_heads=self.config.num_heads,
                d_head=self.config.d_head,
                ffn_mult=self.config.ffn_mult,
                dropout=self.config.dropout,
                rope=self.rope,
            )
            for _ in range(self.config.num_layers)
        ])
        self.final_norm = nn.LayerNorm(D)

        # Attention pooling over valid history tokens for latent SysID & trust heads
        self.history_pool_query = nn.Linear(D, 1)

        # Decoupled output prediction heads
        # 1. Task-specific Kinematics Residual Heads (mean delta + log-variance)
        self.kinematics_delta_head = nn.ModuleDict({
            name: nn.Sequential(
                nn.Linear(D, D),
                nn.GELU(),
                nn.Linear(D, spec.state_dim),
            )
            for name, spec in TASK_INTERFACES.items()
        })
        self.kinematics_logvar_head = nn.ModuleDict({
            name: nn.Sequential(
                nn.Linear(D, D // 2),
                nn.GELU(),
                nn.Linear(D // 2, spec.state_dim),
            )
            for name, spec in TASK_INTERFACES.items()
        })

        # 2. Shared 6-Axis Wrench Dynamics Head (mean + log-variance)
        self.wrench_out_head = nn.Sequential(
            nn.Linear(D, D),
            nn.GELU(),
            nn.Linear(D, 6),
        )
        self.wrench_logvar_head = nn.Sequential(
            nn.Linear(D, D // 2),
            nn.GELU(),
            nn.Linear(D // 2, 6),
        )

        # 3. Five-Class Contact Mode Head (modes 0..4 only)
        self.contact_mode_head = nn.Sequential(
            nn.Linear(D, D // 2),
            nn.GELU(),
            nn.Linear(D // 2, M),
        )

        # 4. Latent Physical System Identification Heads (friction mu & mass kg)
        self.latent_friction_head = nn.Sequential(
            nn.Linear(D, D // 2),
            nn.GELU(),
            nn.Linear(D // 2, 1),
        )
        self.latent_mass_head = nn.Sequential(
            nn.Linear(D, D // 2),
            nn.GELU(),
            nn.Linear(D // 2, 1),
        )

        # 5. Epistemic Trust Score & Conformal Calibration State
        # Note: trust_calibrated remains False until calibrated on held-out calibration residuals.
        self.epistemic_trust_head = nn.Sequential(
            nn.Linear(D, D // 2),
            nn.GELU(),
            nn.Linear(D // 2, 1),
        )
        self.register_buffer("trust_calibrated", torch.tensor(False, dtype=torch.bool))
        self.register_buffer("conformal_quantiles", torch.full((M, len(self.config.prediction_horizons)), float("nan")))

    def _validate_inputs(
        self,
        task: str,
        hist_states: torch.Tensor,
        hist_prev_actions: torch.Tensor,
        hist_wrenches: torch.Tensor,
        hist_mask: torch.Tensor,
        future_actions: torch.Tensor,
        future_action_mask: torch.Tensor,
        prev_mode_probs: Optional[torch.Tensor] = None,
    ) -> None:
        spec = get_task_interface(task)
        if hist_states.ndim != 3 or hist_states.shape[1] != self.config.history_horizon or hist_states.shape[2] != spec.state_dim:
            raise ContractViolationError(
                f"hist_states must have shape (B, {self.config.history_horizon}, {spec.state_dim}) for '{task}', "
                f"got {tuple(hist_states.shape)}"
            )
        B = hist_states.shape[0]
        if hist_prev_actions.shape != (B, self.config.history_horizon, spec.action_dim):
            raise ContractViolationError(
                f"hist_prev_actions must have shape ({B}, {self.config.history_horizon}, {spec.action_dim}), "
                f"got {tuple(hist_prev_actions.shape)}"
            )
        if hist_wrenches.shape != (B, self.config.history_horizon, spec.wrench_dim):
            raise ContractViolationError(
                f"hist_wrenches must have shape ({B}, {self.config.history_horizon}, {spec.wrench_dim}), "
                f"got {tuple(hist_wrenches.shape)}"
            )
        if hist_mask.shape != (B, self.config.history_horizon):
            raise ContractViolationError(f"hist_mask shape mismatch: {tuple(hist_mask.shape)}")
        if not torch.all(hist_mask[:, -1]):
            raise ContractViolationError("Anchor observation hist_mask[:, -1] must be True for every sample.")
        if future_actions.ndim != 3 or future_actions.shape[0] != B or future_actions.shape[2] != spec.action_dim:
            raise ContractViolationError(
                f"future_actions must have shape ({B}, H_fut, {spec.action_dim}), got {tuple(future_actions.shape)}"
            )
        H_fut = future_actions.shape[1]
        if H_fut < 1 or H_fut > self.config.max_future_horizon:
            raise ContractViolationError(
                f"future_actions horizon H_fut={H_fut} must lie in [1, {self.config.max_future_horizon}]"
            )
        if future_action_mask.shape != (B, H_fut):
            raise ContractViolationError(f"future_action_mask shape mismatch: {tuple(future_action_mask.shape)}")

        for name, tensor in (
            ("hist_states", hist_states),
            ("hist_prev_actions", hist_prev_actions),
            ("hist_wrenches", hist_wrenches),
            ("future_actions", future_actions),
        ):
            if not torch.isfinite(tensor).all():
                raise ContractViolationError(f"Non-finite values detected in inference tensor '{name}'.")

        if prev_mode_probs is not None:
            expected_shape = (B, self.config.history_horizon, self.config.num_operational_modes)
            if prev_mode_probs.shape != expected_shape:
                raise ContractViolationError(
                    f"prev_mode_probs must have shape {expected_shape}, got {tuple(prev_mode_probs.shape)}"
                )
            if not torch.isfinite(prev_mode_probs).all():
                raise ContractViolationError("Non-finite values in prev_mode_probs.")

    def _encode_and_decode(
        self,
        task: str,
        hist_states: torch.Tensor,
        hist_prev_actions: torch.Tensor,
        hist_wrenches: torch.Tensor,
        hist_mask: torch.Tensor,
        future_actions: torch.Tensor,
        future_action_mask: torch.Tensor,
        mode_probs_prior: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        spec = get_task_interface(task)
        B, H_hist, _ = hist_states.shape
        H_fut = future_actions.shape[1]
        device = hist_states.device

        task_ids = torch.full((B,), spec.task_index, dtype=torch.long, device=device)
        task_emb = self.task_embedding(task_ids).unsqueeze(1)  # (B, 1, D)

        signals = extract_observable_contact_signals(task, hist_states, hist_wrenches)

        s_emb = self.state_in_proj[task](hist_states)
        pa_emb = self.prev_action_in_proj[task](hist_prev_actions)
        w_emb = self.wrench_in_proj(hist_wrenches)
        sig_emb = self.contact_signal_proj(signals)
        mp_emb = self.mode_prob_in_proj(mode_probs_prior)

        hist_fused = torch.cat((s_emb, pa_emb, w_emb, sig_emb, mp_emb), dim=-1)
        hist_type = self.token_type_embedding(torch.zeros((B, H_hist), dtype=torch.long, device=device))
        hist_tokens = self.history_token_fusion(hist_fused) + task_emb + hist_type

        # Zero out left-padded history token embeddings prior to Transformer
        hist_tokens = hist_tokens * hist_mask.unsqueeze(-1).to(dtype=hist_tokens.dtype)

        # Future candidate-action query tokens
        fut_steps = torch.arange(H_fut, dtype=torch.long, device=device).unsqueeze(0).expand(B, H_fut)
        fut_type = self.token_type_embedding(torch.ones((B, H_fut), dtype=torch.long, device=device))
        fut_tokens = (
            self.future_action_in_proj[task](future_actions)
            + self.future_horizon_embedding(fut_steps)
            + task_emb
            + fut_type
        )
        fut_tokens = fut_tokens * future_action_mask.unsqueeze(-1).to(dtype=fut_tokens.dtype)

        seq = torch.cat((hist_tokens, fut_tokens), dim=1)  # (B, H_hist + H_fut, D)
        attn_mask = build_causal_history_future_mask(hist_mask, future_action_mask)

        for layer in self.layers:
            seq = layer(seq, attn_mask)
        seq = self.final_norm(seq)

        return seq[:, :H_hist, :], seq[:, H_hist:, :]

    def forward(
        self,
        task: str,
        hist_states: torch.Tensor,
        hist_prev_actions: torch.Tensor,
        hist_wrenches: torch.Tensor,
        hist_mask: torch.Tensor,
        future_actions: torch.Tensor,
        future_action_mask: torch.Tensor,
        prev_mode_probs: Optional[torch.Tensor] = None,
        extra_inputs_for_audit: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, torch.Tensor]:
        """Forward pass strictly accepting non-privileged observable inputs."""
        if extra_inputs_for_audit is not None:
            assert_no_privileged_keys_in_inputs(extra_inputs_for_audit)

        self._validate_inputs(
            task=task,
            hist_states=hist_states,
            hist_prev_actions=hist_prev_actions,
            hist_wrenches=hist_wrenches,
            hist_mask=hist_mask,
            future_actions=future_actions,
            future_action_mask=future_action_mask,
            prev_mode_probs=prev_mode_probs,
        )

        B, H_hist, _ = hist_states.shape
        M = self.config.num_operational_modes
        device = hist_states.device

        if prev_mode_probs is None:
            # Causal self-predicted mode feedback:
            # Step 1: evaluate model's own causal mode predictions on history from uniform prior.
            uniform_prior = torch.full((B, H_hist, M), 1.0 / M, dtype=hist_states.dtype, device=device)
            hist_hidden_p1, _ = self._encode_and_decode(
                task, hist_states, hist_prev_actions, hist_wrenches, hist_mask,
                future_actions, future_action_mask, uniform_prior,
            )
            self_mode_probs = F.softmax(self.contact_mode_head(hist_hidden_p1), dim=-1)
            # Shift right by 1 timestep so step tau receives model's own prediction p_hat(m_{tau-1})
            shifted_prior = torch.cat((uniform_prior[:, :1, :], self_mode_probs[:, :-1, :]), dim=1)
        else:
            shifted_prior = prev_mode_probs

        hist_hidden, fut_hidden = self._encode_and_decode(
            task, hist_states, hist_prev_actions, hist_wrenches, hist_mask,
            future_actions, future_action_mask, shifted_prior,
        )

        # Masked attention pooling over valid history tokens ONLY
        pool_scores = self.history_pool_query(hist_hidden).squeeze(-1)  # (B, H_hist)
        pool_scores = pool_scores.masked_fill(~hist_mask.bool(), -1e9)
        pool_weights = F.softmax(pool_scores, dim=-1).unsqueeze(-1)     # (B, H_hist, 1)
        latent_history_repr = torch.sum(hist_hidden * pool_weights, dim=1)  # (B, D)

        # Kinematics delta predictions -> cumulative multi-step physical state rollout from anchor s_t
        anchor_state = hist_states[:, -1, :].unsqueeze(1)  # (B, 1, state_dim)
        pred_state_deltas = self.kinematics_delta_head[task](fut_hidden)  # (B, H_fut, state_dim)
        pred_next_states = anchor_state + torch.cumsum(pred_state_deltas, dim=1)
        pred_state_logvar = torch.clamp(self.kinematics_logvar_head[task](fut_hidden), min=-10.0, max=6.0)

        # Wrench dynamics predictions
        pred_next_wrenches = self.wrench_out_head(fut_hidden)
        pred_wrench_logvar = torch.clamp(self.wrench_logvar_head(fut_hidden), min=-10.0, max=6.0)

        # 5-class operational contact mode predictions
        pred_mode_logits = self.contact_mode_head(fut_hidden)
        pred_mode_probs = F.softmax(pred_mode_logits, dim=-1)
        hist_mode_logits = self.contact_mode_head(hist_hidden)

        # Latent physical system identification (strictly from causal history representation)
        pred_friction_mu = F.softplus(self.latent_friction_head(latent_history_repr).squeeze(-1)) + 1e-4
        pred_mass_kg = F.softplus(self.latent_mass_head(latent_history_repr).squeeze(-1)) + 1e-4

        # Predictive uncertainty & epistemic trust score
        state_std_mean = torch.mean(torch.exp(0.5 * pred_state_logvar), dim=-1)
        wrench_std_mean = torch.mean(torch.exp(0.5 * pred_wrench_logvar), dim=-1)
        pred_uncertainty_total = state_std_mean + wrench_std_mean  # (B, H_fut)
        pred_epistemic_trust_score = torch.sigmoid(self.epistemic_trust_head(latent_history_repr).squeeze(-1))

        return {
            "pred_next_states": pred_next_states,
            "pred_state_deltas": pred_state_deltas,
            "pred_state_logvar": pred_state_logvar,
            "pred_next_wrenches": pred_next_wrenches,
            "pred_wrench_logvar": pred_wrench_logvar,
            "pred_mode_logits": pred_mode_logits,
            "pred_mode_probs": pred_mode_probs,
            "hist_mode_logits": hist_mode_logits,
            "pred_friction_mu": pred_friction_mu,
            "pred_mass_kg": pred_mass_kg,
            "pred_epistemic_trust_score": pred_epistemic_trust_score,
            "pred_uncertainty_total": pred_uncertainty_total,
            "latent_history_repr": latent_history_repr,
        }

    def predict_with_uncertainty(
        self,
        task: str,
        hist_states: torch.Tensor,
        hist_prev_actions: torch.Tensor,
        hist_wrenches: torch.Tensor,
        hist_mask: torch.Tensor,
        future_actions: torch.Tensor,
        future_action_mask: torch.Tensor,
        prev_mode_probs: Optional[torch.Tensor] = None,
    ) -> Dict[str, Any]:
        """Read-only inference wrapper exposing predictions, uncertainty, and conformal status."""
        self.eval()
        with torch.no_grad():
            out = self.forward(
                task=task,
                hist_states=hist_states,
                hist_prev_actions=hist_prev_actions,
                hist_wrenches=hist_wrenches,
                hist_mask=hist_mask,
                future_actions=future_actions,
                future_action_mask=future_action_mask,
                prev_mode_probs=prev_mode_probs,
            )
            out["trust_calibrated"] = bool(self.trust_calibrated.item())
            out["conformal_quantiles"] = self.conformal_quantiles.clone()
            return out

    def attach_conformal_quantiles(self, quantile_matrix: torch.Tensor) -> None:
        """Attaches calibrated conformal quantiles Q in R^{5 x 5} (read-only post-calibration)."""
        expected = (self.config.num_operational_modes, len(self.config.prediction_horizons))
        q = torch.as_tensor(quantile_matrix, dtype=self.conformal_quantiles.dtype, device=self.conformal_quantiles.device)
        if q.shape != expected:
            raise ContractViolationError(f"Expected conformal quantile shape {expected}, got {tuple(q.shape)}")
        if not torch.isfinite(q).all():
            raise ContractViolationError("Conformal quantiles must be finite.")
        self.conformal_quantiles.copy_(q)
        self.trust_calibrated.fill_(True)

    def parameter_summary(self) -> Dict[str, Any]:
        """Returns exact parameter counts across submodules."""
        total = sum(p.numel() for p in self.parameters())
        trainable = sum(p.numel() for p in self.parameters() if p.requires_grad)
        by_module = {}
        for name, mod in self.named_children():
            by_module[name] = sum(p.numel() for p in mod.parameters())
        return {
            "total_parameters": int(total),
            "trainable_parameters": int(trainable),
            "d_model": self.config.d_model,
            "num_layers": self.config.num_layers,
            "num_heads": self.config.num_heads,
            "d_head": self.config.d_head,
            "module_breakdown": by_module,
        }
