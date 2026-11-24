# HOROS PROBE-1 — P5-T4R4 Preload Sweep & Adaptive Preload Specification
## Controlled Preload Sweep, Causal Friction Probing, & Adaptive Clamping Validation

**Project:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1  
**Milestone:** P5-T4R4  
**Date:** November 2026  
**Auditor:** HOROS Physical Science & Verification Core  

---

### 1. Research Objectives

1. **Systematic Preload Sweep:** Evaluate fixed initial normal preloads $F_{n,0} \in \{3.0, 5.0, 8.0, 12.0, 16.0, 20.0, 25.0\}\,\text{N}$ across contact topologies $N \in \{3, 4, 5\}$ and surface friction $\mu \in \{0.35, 0.55, 0.75\}$ under 10 distinct disturbance regimes.
2. **Causal Pre-Contact Friction Estimation:** Develop a low-energy active micro-probing estimator that observes tactile force $F_t(\delta_t)$ under normal load $F_{n,\text{probe}} = 4.0\,\text{N}$ to estimate friction $\hat{\mu}$, contact stiffness $\hat{k}_t$, and estimation uncertainty $\sigma_\mu$ without accessing ground-truth simulator parameters.
3. **Adaptive Preload Planner:** Formulate a physics-grounded planner that maps estimated friction and uncertainty to the minimum sufficient safe preload:
   $$F_{n}^* = \text{clip}\left(\frac{\max(|F_{\text{ext}}|, F_{t,\text{est}})}{\hat{\mu} - 1.96\sigma_\mu} + m_{\text{safe}}, F_{n,\min}, F_{n,\max}\right)$$
4. **Comprehensive Baseline Comparison:** Compare the adaptive planner against 9 baseline configurations across 600 held-out evaluation episodes.
5. **Gateway Acceptance:** Determine whether adaptive preloading achieves statistical superiority or equivalent stability with substantially reduced mechanical work and impulse without violating physical constraints.

---

### 2. Physical Disturbance Regimes

The benchmark includes 10 matched disturbance regimes:
1. `shear_ramp`: Linear shear velocity ramp ($v_{\text{shear}} \in [1.0, 5.0]\,\text{mm/s}$).
2. `fast_ramp`: High-speed shear ramp ($v_{\text{shear}} = 8.0\,\text{mm/s}$) challenging actuator rate bandwidth.
3. `tangential_shock`: Sudden pulse disturbance ($2.5 \times$ nominal velocity for $50\,\text{ms}$).
4. `micro_probe`: Cyclic $10\,\text{Hz}$ tangential perturbation ($0.5\,\text{mm}$ amplitude).
5. `normal_perturbation`: $30\%$ normal force dip simulating compliance release.
6. `torque_pulse`: Rotational disturbance torque ($\tau_z \in [0.05, 0.20]\,\text{Nm}$).
7. `unequal_friction`: Friction variation across contacts ($\Delta \mu \pm 0.15$).
8. `unequal_stiffness`: Fingertip compliance variation ($\Delta E^* \pm 20\%$).
9. `off_center_load`: Load offset causing coupled shear and torque ($y_{\text{offset}} = 15\,\text{mm}$).
10. `mass_inertia_variation`: Workpiece mass variation ($m \in [0.3, 0.8]\,\text{kg}$).

---

### 3. Actuator Bounds and Conservation Constraints

- Rate limit: $|\dot{F}_{n,i}| \le 50.0\,\text{N/s}$ ($0.05\,\text{N/ms}$ at $dt = 1\,\text{ms}$).
- Individual bounds: $F_{n,i} \in [3.0, 25.0]\,\text{N}$.
- Total budget (Protocol B): $\sum F_{n,i} \le N_{\text{active}} \times 8.0\,\text{N}$.
- Wrench conservation: $\sum \mathbf{f}_i + \mathbf{w}_{\text{ext}} = m\mathbf{a}_{\text{obj}}$. Residual norm must be $< 10^{-4}\,\text{N}$.
- Causal observability: Telemetry carries calibrated Gaussian sensor noise ($\sigma_F = 20\,\text{mN}, \sigma_\delta = 0.5\,\mu\text{m}, \sigma_\theta = 1\,\text{mrad}$). Ground-truth values are strictly hidden.
