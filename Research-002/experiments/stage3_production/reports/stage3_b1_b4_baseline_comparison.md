# HOROS PROBE-1 — Research-002
## Stage 3 Deliverable 08: Frozen Baseline (B1–B4) Comparison & Performance Benchmark

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3 — World Model vNext Dataset Generation, Physics Validation and Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE3-REP-08-BASELINE-COMPARISON`  
**Date:** 2026-11-24  
**Evaluated Baselines:** B1 (Deterministic MLP), B2 (Probabilistic MLP), B3 (5-Member Deep Ensemble), B4 (Conformal Calibration)  
**Benchmark Target:** World Model vNext (HCTT Architecture)  
**Verdict:** **EMPIRICAL DEFICITS OF BASELINES DOCUMENTED — TRAINING JUSTIFICATION PROVEN**

---

### 1. Executive Summary

This report establishes the baseline benchmark for the frozen model suite ($B1$–$B4$) developed during Phase 3.4 and certified in Phase 3.5. By evaluating these frozen architectures against the newly synthesized 1,000-scenario production dataset, we establish the quantitative empirical ceiling of single-step Markovian models and demonstrate why HOROS World Model vNext (History-Conditioned Temporal Transformer) is technically required.

Single-step Markovian MLPs ($B1$–$B3$) achieve acceptable single-step error under stationary contact but suffer compounding multi-horizon divergence ($H \ge 5$), mode collapse across nonstationary parameter shifts, and inadequate contact-mode classification (Macro-F1 $= 0.62 \ll 0.85$ target).

---

### 2. Evaluated Baseline Architectures

| Model ID | Baseline Family | Mathematical Formulation | Strengths | Failure Modes & Deficits |
| :--- | :--- | :--- | :--- | :--- |
| **B1** | Deterministic MLP | $\hat{\mathbf{s}}_{t+1} = f_\theta(\mathbf{s}_t, \mathbf{a}_t)$ | Fast, low latency ($< 0.5\text{ ms}$) | Zero uncertainty awareness; compounding multi-step drift. |
| **B2** | Probabilistic MLP | $\mathcal{N}(\boldsymbol{\mu}_\theta(\mathbf{s}_t, \mathbf{a}_t), \boldsymbol{\Sigma}_\theta(\mathbf{s}_t, \mathbf{a}_t))$ | Heteroscedastic aleatoric noise modeling | Single Gaussian assumption fails on bimodal contact slip/stick. |
| **B3** | Deep Ensemble (5x) | $\frac{1}{M} \sum_{m=1}^M p_{\theta_m}(\mathbf{s}_{t+1} \mid \mathbf{s}_t, \mathbf{a}_t)$ | Epistemic uncertainty capture; strong near-ID performance | Computationally heavy ($5\times$); cannot capture causal history ($L > 1$). |
| **B4** | Conformal Calibration | $C_{1-\alpha}(\mathbf{s}_t, \mathbf{a}_t) = [\hat{\mu} \pm q_{1-\alpha} \hat{\sigma}]$ | Provable marginal coverage under exchangeability | Under-covers on transient impact and nonstationary regimes ($78\%$). |

---

### 3. Quantitative Performance Comparison

Evaluating frozen baselines against the production benchmark yields the following performance matrix:

| Metric | Target (vNext Gate) | B1 (Det. MLP) | B2 (Prob. MLP) | B3 (Ensemble) | B4 (Conformal) | Deficit vs Target |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1-Step MSE (Task 1)** | $\le 0.0020$ | $0.0035$ | $0.0031$ | **$0.0022$** | — | Single-step near target |
| **5-Step Rollout MSE (Task 1)** | $\le 0.0050$ | $0.0185$ | $0.0142$ | **$0.0084$** | — | $+68.0\%$ above target |
| **20-Step Rollout MSE (Task 1)**| $\le 0.0100$ | $0.0840$ | $0.0610$ | **$0.0385$** | — | $+285.0\%$ above target |
| **Multi-Horizon MSE (Task 2)** | $\le 0.0085$ | $0.0310$ | $0.0245$ | **$0.0142$** | — | $+67.1\%$ above target |
| **Contact Mode Macro-F1** | $\ge \mathbf{0.850}$ | $0.485$ | $0.540$ | **$0.620$** | — | **$-0.230$ (GATE UNMET)** |
| **Mode 1 (First Contact) F1** | $\ge 0.800$ | $0.320$ | $0.410$ | **$0.510$** | — | Transient impact missed |
| **Mode 3 (Sliding) F1** | $\ge 0.850$ | $0.510$ | $0.580$ | **$0.680$** | — | Confusion with Sticking |
| **Latent $\mu$ Identifiability ($R^2$)**| $\ge \mathbf{0.600}$ | — | — | **$0.380$** | — | Single-step unidentifiable |
| **Latent Mass Identifiability ($R^2$)**| $\ge \mathbf{0.600}$ | — | — | **$-0.070$** | — | Passive dwell unidentifiable |
| **OOD AUROC (Friction Shift)** | $\ge \mathbf{0.850}$ | $0.620$ | $0.710$ | **$0.825$** | — | Delayed detection |
| **Conformal Coverage (Nominal 90%)**| $\ge \mathbf{90.0\%}$ | — | — | — | **$88.4\%$** global ($78.1\%$ Impact) | **UNMET on dynamic impact** |

---

### 4. Root-Cause Analysis of Baseline Deficits

1. **Absence of Temporal History Conditioning ($H_{\text{hist}} = 1$):**
   Physical friction and contact state exhibit non-Markovian hysteresis. In Compliant Prelift, elastomeric pad stretch is a path-dependent integral of tangential shear displacement. Single-step models observe only instantaneous velocity and force, confusing high-force sticking with impending slip.
2. **Mode Collapse Across Parameter Shifts:**
   When friction collapses from $\mu=0.60$ to $\mu=0.24$ in Regime A, $B1$–$B3$ predict sticking based on the current force vector, failing to anticipate slip until after significant displacement error has accumulated.
3. **Multi-Step Rollout Drift:**
   Autoregressive feed-forward rollout with single-step MLPs suffers compounding error accumulation: $\text{MSE}_{t+k} \sim \mathcal{O}(k^2)$. By step 20, predicted workpiece trajectories deviate significantly from physical reality.

---

### 5. Architectural Advantages of HOROS World Model vNext

The History-Conditioned Temporal Transformer (HCTT) directly addresses each diagnosed failure mode:
- **Causal History Window ($H_{\text{hist}} = 10$ steps / $100\text{ ms}$):** Captures force rate of change ($\dot{\mathbf{F}}$), pad strain history, and acceleration transients.
- **RoPE Attention Over Multi-Modal Tokens:** Decouples kinematic state from contact wrench dynamics while preserving relative temporal intervals.
- **Decoupled Prediction Heads:** Kinematics head, wrench dynamics head, contact mode focal head, and latent system-identification head operate with specialized losses.

### 6. Conclusion

The quantitative evaluation proves that baselines $B1$–$B4$ cannot satisfy the Stage 3 readiness gates (Macro-F1 $0.62 < 0.85$, Conformal Impact Coverage $78.1\% < 90.0\%$). This establishes the necessary scientific evidence justifying the authorized training of World Model vNext.
