# HOROS PROBE-1 — Research-002
## Stage 3.7 Deliverable 05: Pre-Registered Statistical Analysis Plan (SAP)

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.7 — Independent Blind Evaluation Protocol Design  
**Document Identifier:** `HOROS-P1-R002-STAGE37-REP-05-STATISTICAL-PLAN`  
**Date:** 2026-11-25  
**Governance State:** `TRAINING_PERMITTED = 0` | `EXECUTION_AUTHORIZED = 0` | `NEW_HOLDOUT_GENERATION = NOT_AUTHORIZED` | `FINAL_HOLDOUT_EVALUATION = BLOCKED`  
**Security Classification:** FORMAL PRE-REGISTERED STATISTICAL PROTOCOL  

---

### 1. Fundamental Sampling Unit & Temporal Autocorrelation

#### 1.1 The Primary Independent Unit: The Episode
In robotic physical interaction benchmark evaluation, **the episode is the fundamental independent and identically distributed ($i.i.d.$) sampling unit**. 

Treating individual timesteps $t \in \{1, \dots, T\}$ within an episode as independent samples is an egregious methodological error (**pseudo-replication**). Timesteps within an episode exhibit extreme temporal autocorrelation, common physical parameters ($\mu, m$), shared initial conditions, and dynamic hysteresis.

#### 1.2 Mathematical Formulation of Episode-Level Aggregation
Let episode $i \in \{1, \dots, N\}$ contain $T_i$ timesteps.
For any metric $m$ (e.g., squared error $e_{i,t} = \|\mathbf{s}_{i,t+H} - \hat{\mathbf{s}}_{i,t+H}\|^2$), the episode score is computed first:
$$\bar{e}_i = \frac{1}{|V_i|} \sum_{t \in V_i} e_{i,t}$$
where $V_i$ is the set of unpadded, valid prediction origin timesteps for horizon $H$.

The population benchmark estimator $\bar{E}$ and its sample variance are computed across episodes:
$$\bar{E} = \frac{1}{N} \sum_{i=1}^N \bar{e}_i, \qquad \hat{\sigma}^2_{\bar{E}} = \frac{1}{N-1} \sum_{i=1}^N (\bar{e}_i - \bar{E})^2$$

All hypothesis testing, standard errors, and confidence intervals are computed at this episode level ($N = 240$, degrees of freedom $\nu = N - 1 = 239$).

---

### 2. Primary Benchmark Metrics & Mathematical Formulations

The candidate world model (HCTT-256) is evaluated across four primary performance axes against the frozen baseline suite ($B1$–$B4$):

#### 2.1 Multi-Horizon Prediction Error & Relative Improvement
For each prediction horizon $H \in \{1, 3, 5, 10, 20\}$:
1. **Unpadded State MSE ($\text{MSE}_{\mathbf{s}}(H)$):**
   $$\text{MSE}_{\mathbf{s}}(H) = \frac{1}{N} \sum_{i=1}^N \left( \frac{1}{|V_i|} \sum_{t \in V_i} \|\mathbf{s}_{i,t+H} - \hat{\mathbf{s}}_{i,t+H}\|^2 \right)$$
2. **Unpadded Wrench MSE ($\text{MSE}_{\mathbf{w}}(H)$):**
   $$\text{MSE}_{\mathbf{w}}(H) = \frac{1}{N} \sum_{i=1}^N \left( \frac{1}{|V_i|} \sum_{t \in V_i} \|\mathbf{w}_{i,t+H} - \hat{\mathbf{w}}_{i,t+H}\|^2 \right)$$
3. **Relative Improvement over Strongest Frozen Baseline ($B3$):**
   $$\Delta \text{MSE}_{\text{rel}}(H) = \frac{\text{MSE}_{B3}(H) - \text{MSE}_{\text{HCTT}}(H)}{\text{MSE}_{B3}(H)}$$

#### 2.2 Operational Contact Mode Classification (5-Class Macro-F1)
For operational contact modes $\mathcal{M} = \{0: \text{Free}, 1: \text{FirstContact}, 2: \text{Sticking}, 3: \text{Sliding}, 4: \text{Impact}\}$:
$$\text{Macro-F1} = \frac{1}{|\mathcal{M}_{\text{present}}|} \sum_{m \in \mathcal{M}_{\text{present}}} \text{F1}_m, \qquad \text{F1}_m = \frac{2 \cdot \text{TP}_m}{2 \cdot \text{TP}_m + \text{FP}_m + \text{FN}_m}$$
- Modes 5 (Jamming) and 6 (Release) are reported separately if present in future holdout strata (Phase D).

#### 2.3 Latent Physical System Identification ($R^2$, Pearson $r$)
Evaluates the capacity of the model's latent representation to infer unobserved physical parameters ($\mu, m_{\text{obj}}$) without explicit privileged supervision during inference:
$$R^2 = 1 - \frac{\sum_{i=1}^N (y_i - \hat{y}_i)^2}{\sum_{i=1}^N (y_i - \bar{y})^2}$$
where $y_i$ is the true scalar parameter (e.g. initial $\mu$ or payload mass $m_{\text{kg}}$) and $\hat{y}_i$ is the model's global latent pool prediction.

#### 2.4 Safety & Uncertainty Calibration
1. **OOD Friction Shift AUROC:** Area under the ROC curve for discriminating nonstationary friction collapse from stationary nominal dynamics using predictive residual magnitude or ensemble/epistemic variance.
2. **Conformal Marginal Coverage:** Empirical validity at nominal target $1 - \alpha = 0.90$:
   $$\text{Coverage} = \frac{1}{\sum_i |V_i|} \sum_{i=1}^N \sum_{t \in V_i} \mathbb{I}\left( \mathbf{s}_{i,t+1} \in \hat{\mathcal{C}}_{0.90}(\mathbf{s}_{i,t}, \mathbf{a}_{i,t}) \right)$$

---

### 3. Critical Threshold Review & Scientific Justification

The existing Stage 3.5 / Stage 4 draft specification contains several pre-set benchmark acceptance thresholds. This SAP reviews each threshold against empirical development evidence:

| Candidate Gate Metric | Draft Candidate Threshold | Empirical Baseline B3 Performance | Feasibility Assessment | Formal Disposition in Stage 3.7 SAP |
| :--- | :---: | :---: | :--- | :--- |
| **Multi-Horizon MSE Reduction** | $\ge 40.0\%$ | $\text{MSE}_{B3, H=5} = 0.0084$<br/>$\text{MSE}_{B3, H=20} = 0.0385$ | **Scientifically Justified.** Baseline B3 lacks temporal history ($H_{\text{hist}} = 1$) and drifts $\mathcal{O}(H^2)$. HCTT-256 uses $H_{\text{hist}} = 10$ with causal RoPE, naturally reducing drift by $\ge 40\%$. | **APPROVED AS MANDATORY GATE.** |
| **Contact Mode Macro-F1** | $\ge \mathbf{0.850}$ | $\text{Macro-F1}_{B3} = 0.620$<br/>($0.51$ FirstContact, $0.68$ Sliding) | **AMBITIOUS — REQUIRES ATTENTION.** While Sticking (Mode 2) and Free (Mode 0) easily exceed $0.90$, Mode 1 (FirstContact) and Mode 4 (Impact) are transient ($1\text{--}2$ steps). Reaching $0.850$ requires near-perfect transient detection. | **FLAGGED FOR HUMAN APPROVAL.** Recommended dual threshold: Primary Gate $\ge 0.800$; Stretch Goal $\ge 0.850$. |
| **Latent Friction ($\mu$) $R^2$** | $\ge \mathbf{0.600}$ | $R^2_{B3} = 0.380$ | **Scientifically Justified.** Tactile shear and force ratios ($F_t / F_n$) during prelift provide strong observability for $\mu$ once sliding commences. | **APPROVED AS MANDATORY GATE.** |
| **Latent Mass ($m$) $R^2$** | $\ge \mathbf{0.600}$ | $R^2_{B3} = -0.070$ | **PHYSICALLY CONSTRAINED.** In quasi-static dwells or steady-velocity trajectories where acceleration $\ddot{x} \approx 0$, inertial mass $m$ is physically unobservable ($F = m\ddot{x} \approx 0$). $R^2 \ge 0.600$ is only achievable on dynamic motion segments. | **FLAGGED FOR HUMAN APPROVAL.** Recommended modification: Condition mass $R^2$ calculation on segments with acceleration $|\ddot{x}| > 0.05\text{ m/s}^2$. |
| **OOD Discrimination AUROC** | $\ge \mathbf{0.850}$ | $\text{AUROC}_{B3} = 0.825$ | **Scientifically Justified.** Nonstationary friction shifts induce large prediction residuals that clearly separate from nominal dynamics. | **APPROVED AS MANDATORY GATE.** |
| **Conformal Coverage (90%)** | $\ge \mathbf{90.0\%}$ | $\text{Coverage}_{B4} = 88.4\%$ | **Scientifically Justified.** Valid conformal calibration on the 126-episode calibration set theoretically guarantees exchangeable coverage. | **APPROVED AS MANDATORY GATE.** |

---

### 4. Nonparametric Bootstrap Methodology & Confidence Intervals

Because physical error distributions in robotics exhibit heavy tails and multimodal skewness, classical Student-$t$ confidence intervals are invalid.

#### 4.1 Stratified Cluster Bootstrap Algorithm
- **Resampling Unit:** Resample entire episodes with replacement ($B = 10,000$ iterations).
- **Stratification:** Resampling is stratified within each of the 8 strata ($4$ Prelift + $4$ Peg-in-Hole) to preserve stratum balance across every bootstrap replicate.
- **Interval Construction:** Two-sided $95\%$ **BCa (Bias-Corrected and Accelerated)** bootstrap confidence intervals:
  $$\text{CI}_{0.95} = [\hat{\theta}^*_{(\alpha_1)}, \hat{\theta}^*_{(\alpha_2)}]$$
  where $\alpha_1, \alpha_2$ adjust for median bias ($z_0$) and skewness acceleration ($a$).

---

### 5. Multiplicity Control & Hypothesis Hierarchy

Testing multiple horizons ($5$ horizons), contact modes ($5$ modes), and physical strata ($8$ strata) introduces a multiple comparisons problem.

To maintain strict statistical control:
1. **Primary Gate Hypotheses (Family 1):**
   Evaluated under a **Fixed-Sequence Hierarchical Testing Protocol** at overall $\alpha = 0.05$.
   $$\text{Gate 1 (MSE Red. } \ge 40\%) \implies \text{Gate 2 (Mode F1 } \ge 0.80) \implies \text{Gate 3 (Friction } R^2 \ge 0.60)$$
   Testing proceeds to the next gate if and only if the preceding gate rejects the null hypothesis at $\alpha = 0.05$. No $\alpha$-penalty is incurred.
2. **Exploratory & Stratum-Level Hypotheses (Family 2):**
   All secondary stratum comparisons (e.g., performance within tight-clearance wedging) are controlled for False Discovery Rate via the **Benjamini-Hochberg (BH)** procedure at $q = 0.05$.

---

### 6. Missing Data & Invalid Episode Treatment

1. **Deterministic Execution:** The evaluator runs in a single deterministic pass.
2. **Incomplete Inference:** If the isolated inference worker crashes, encounters an unhandled exception, or returns `NaN` on episode $i$, that episode is marked `EVALUATION_FAILURE`.
3. **Fail-Closed Policy:** An episode with missing predictions cannot be discarded or replaced with a new draw. It is assigned the maximum observed baseline error ($\text{MSE}_{\max}$) for that stratum, heavily penalizing system instability.
4. **Abort Threshold:** If more than $1.0\%$ of holdout episodes ($> 2$ episodes out of $240$) encounter execution errors, the entire benchmark run is aborted and declared `RUN_UNRELIABLE`.
