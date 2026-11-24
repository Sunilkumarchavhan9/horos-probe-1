# Preload Research Cross-Milestone Synthesis & Physical Analysis
## The Deterministic Role of Initial Normal Force in Grasp Stability

**Project:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1  
**Scope:** P4-T4B through P5-T4R2 Cross-Milestone Investigation  

---

### 1. The Core Scientific Dilemma Across PROBE-1 Milestones

Across six successive research milestones (P4-T4, P4-T4B, P5-T1, P5-T2, P5-T3, P5-T4, P5-T4R2), a persistent empirical pattern has dominated robotic grasp stability:

1. **Fixed Low Preload ($3.0\,\text{N}$–$5.0\,\text{N}$):**
   - In P4-T4B, fixed $3\,\text{N}$ clamping produced **0.0% grasp success** on low-friction surfaces ($\mu = 0.35$).
   - The friction capacity $\mu F_n = 0.35 \times 3.0 = 1.05\,\text{N}$ was instantly overwhelmed by even modest shear disturbances ($F_t \ge 1.5\,\text{N}$).
2. **Brute-Force High Preload ($20.0\,\text{N}$–$25.0\,\text{N}$):**
   - In P5-T4 revision, `preload_25` achieved **37.63% to 39.93% feasible recovery**, outperforming complex predictive and topology-aware controllers.
   - At $25.0\,\text{N}$, tangential capacity reaches $\mu F_n = 0.35 \times 25.0 = 8.75\,\text{N}$ per contact ($17.5\,\text{N}$ across 2 surviving contacts), arresting workpiece motion before macroscopic slip occurs.
   - **However**, high preload incurs severe mechanical penalties:
     - Normal force impulse rises from $6.29\,\text{Ns}$ (fixed $3\,\text{N}$) to $>31.0\,\text{Ns}$.
     - Damage proxy index jumps from $0.0\,\text{N}^2\text{s}$ to $>108.0\,\text{N}^2\text{s}$.
     - Actuator electrical and mechanical work increases by orders of magnitude.
3. **The Topology Starvation Failure (P5-T4R2):**
   - In P5-T4R2, the proposed `topology_14` controller attempted to be "force-efficient" by commanding only $14.0\,\text{N}$ per contact.
   - Because $14\,\text{N}$ provided only $4.9\,\text{N}$ of friction resistance, surviving contacts slipped under disturbance wrenches, accelerating the object past the $25\,\text{mm}$ drop boundary ($16.17\%$ recovery vs $39.93\%$ for `preload_25`).

---

### 2. Physical Cross-Milestone Evidence Matrix

| Milestone | Controller Policy | Initial $F_{n,0}$ | Realized Mean $F_n$ | Grasp Success Rate | Mean Impulse ($I_n$) | Damage Index ($D_{\text{proxy}}$) | Key Physical Insight |
|---|---|:---:|:---:|:---:|:---:|:---:|---|
| **P4-T4B** | Fixed 3 N | $3.0\,\text{N}$ | $3.00\,\text{N}$ | 0.0% (low-$\mu$) | $6.29\,\text{Ns}$ | $0.00\,\text{N}^2\text{s}$ | Insufficient friction capacity; instant slip |
| **P4-T4B** | Fixed 12 N | $12.0\,\text{N}$ | $12.00\,\text{N}$ | 32.0% (low-$\mu$) | $12.00\,\text{Ns}$ | $0.00\,\text{N}^2\text{s}$ | Modest stability gain without force escalation |
| **P4-T4B** | HOROS Hybrid | $3.0\,\text{N}$ | $14.62\,\text{N}$ | 44.0% (low-$\mu$) | $31.10\,\text{Ns}$ | $108.42\,\text{N}^2\text{s}$ | Succeeded only by emergency clamping to $>20\,\text{N}$ |
| **P5-T1** | Adaptive Micro-Probe | $4.0$–$14.0\,\text{N}$ | $8.40\,\text{N}$ | 68.0% | $14.20\,\text{Ns}$ | $3.12\,\text{N}^2\text{s}$ | Micro-probe enabled friction-matched preload |
| **P5-T2** | Cooperative Balancing | $6.0$–$18.0\,\text{N}$ | $11.20\,\text{N}$ | 71.3% | $18.50\,\text{Ns}$ | $8.45\,\text{N}^2\text{s}$ | Asymmetric preload matched contact friction variation |
| **P5-T3** | Uncertainty-Aware N-Contact | Squeeze share | $20.22\,\text{N}$ | 36.7% (Protocol B) | $24.80\,\text{Ns}$ | $22.10\,\text{N}^2\text{s}$ | High baseline squeeze provided margin |
| **P5-T4** | Original Feasibility Aware | Dynamic solve | $4.89\,\text{N}$ | 9.79% (Protocol B) | $7.10\,\text{Ns}$ | $0.20\,\text{N}^2\text{s}$ | Severe under-preloading starved surviving contacts |
| **P5-T4R2** | `preload_25` | $25.0\,\text{N}$ | $37.16\,\text{N}$ (total) | 39.93% (Protocol A) | $32.40\,\text{Ns}$ | $94.20\,\text{N}^2\text{s}$ | Brute-force clamping wins by margin, not intelligence |
| **P5-T4R2** | `topology_14` | $14.0\,\text{N}$ | $27.07\,\text{N}$ (total) | 16.17% (Protocol A) | $19.80\,\text{Ns}$ | $12.40\,\text{N}^2\text{s}$ | Lower force caused slip, velocity spike, and drop |

---

### 3. Key Findings for Phase 5 Corrective Research

1. **Preload is the Dominant Factor in Grasp Survival:**
   Complex online reactive and predictive controllers cannot rescue an under-preloaded grasp once high-speed disturbance shear begins, because actuator slew ($\le 50\,\text{N/s}$) is too slow to build normal force before macroscopic slip occurs.
2. **The Goal of Adaptive Preload Planning:**
   Instead of defaulting to $25\,\text{N}$ brute force across all scenarios (which maximizes mechanical damage and energy consumption), an intelligent physical agent must observe pre-contact tactile compliance, estimate friction and disturbance bounds, and command the **minimum safe preload** $F_{n,\text{safe}} = \frac{|F_{t,\text{pred}}|}{\hat{\mu} - k\sigma_\mu} + m_{\text{safe}}$.
3. **Requirement for Non-Trivial Preload Policy:**
   The adaptive planner must not be a hardcoded lookup table. It must demonstrate genuine physical inference from pre-contact tactile probing, uncertainty quantification, and dynamic force allocation under actuator rate limits.
