# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 05: Matched-Timestep & Physical-Time Baseline Benchmark

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-05-TIMESTEP-BENCHMARK`  
**Date:** 2026-11-25  

---

### 1. Timestep Provenance Audit of Frozen Phase 3.4 Baselines

Inspection of `src/environment/compliant_prelift_env.py`, `src/environment/peg_in_hole_env.py`, and `src/integrated/phase34_pipeline.py` establishes a critical physical-timestep boundary between tasks in the frozen Phase 3.4 `B1–B4` checkpoints:

| Task | Frozen Phase 3.4 Checkpoint $\Delta t$ | Stage 3.3 / 3.5 Dataset $\Delta t$ | Comparability Classification |
| :--- | :---: | :---: | :--- |
| **`compliant_prelift`** | **`0.01 s`** | **`0.01 s`** | **NATIVE MATCH** (1 step = $10\text{ ms}$ on both; direct horizon comparison valid) |
| **`peg_in_hole`** | **`0.02 s`** | **`0.01 s`** | **INCOMPATIBLE STEP-FOR-STEP $\Delta t$** (1 Phase 3.4 step = $20\text{ ms}$ vs 1 Stage 3.5 step = $10\text{ ms}$) |

**Policy Enforced:**
1. Frozen Phase 3.4 checkpoints are **never retrained or modified**.
2. `compliant_prelift` `B1–B3` (`M4_acmm`) are evaluated directly on strict Stage 3.5 `val` ($T=40$, $\Delta t=0.01\text{ s}$).
3. `peg_in_hole` step-for-step (`k` steps vs `k` steps) comparison across $\Delta t=0.02\text{ s}$ and $\Delta t=0.01\text{ s}$ is **prohibited** as invalid. Instead:
   - Native $\Delta t=0.01\text{ s}$ **Persistence** and **Constant-Velocity** references are benchmarked on Stage 3.5 `val` strict windows (`evaluate_persistence_and_const_vel_baselines`).
   - Frozen `peg_in_hole` `B1–B3` (`M4_acmm`) are evaluated on native $\Delta t=0.02\text{ s}$ episodes (`PHASE3_SEED_POOLS['offline_test_peg_in_hole']`, 40 seeds `3080..3119`, `n_rollouts = 440`) at matched **physical times** ($0.02\text{ s}, 0.06\text{ s}, 0.10\text{ s}, 0.20\text{ s}$).
4. **Blind Holdout Protection:** `final_test` (`final_holdout/`) was **not evaluated** (`final_holdout_evaluated = False`). All Stage 3.5 benchmarks below use strictly `val` or historical Phase 3 `offline_test_peg_in_hole`.

---

### 2. Compliant Prelift Benchmark at Matched $\Delta t = 0.01\text{ s}$ (`val` Strict Windows)

Evaluated on all `76` Stage 3.5 `prelift_long` `val` episodes ($T=40$, strict `10-step` history): `n=2,356` (`H=1`, $0.01\text{ s}$), `n=2,204` (`H=3`, $0.03\text{ s}$), `n=2,052` (`H=5`, $0.05\text{ s}$), `n=1,672` (`H=10`, $0.10\text{ s}$), `n=912` (`H=20`, $0.20\text{ s}$):

| Model / Reference ($\Delta t = 0.01\text{ s}$) | Status | `H=1` (`0.01s`) MSE | `H=3` (`0.03s`) MSE | `H=5` (`0.05s`) MSE | `H=10` (`0.10s`) MSE | `H=20` (`0.20s`) MSE | Mode F1 (All 5) | Mode F1 (Present) | OOD AUROC |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Persistence Baseline** | Native $\Delta t=0.01$ | `0.000414` | `0.002982` | `0.008007` | `0.031608` | `0.125484` | — | — | — |
| **Constant-Velocity Baseline** | Native $\Delta t=0.01$ | **`0.000266`** | **`0.001335`** | **`0.003397`** | **`0.013967`** | **`0.075635`** | — | — | — |
| **B1 (`M4_acmm`, Frozen)** | Frozen ($\Delta t=0.01$) | `0.077927` | `0.978779` | `3.574071` | `23.500819` | `120.998220` | **0.3144** | **0.7860** | 0.5000 |
| **B2 (`M4_acmm`, Frozen)** | Frozen ($\Delta t=0.01$) | `0.026538` | `0.492079` | `1.992798` | `14.407692` | `97.330033` | 0.2560 | 0.6399 | 0.0873 |
| **B3 (`M4_acmm`, Frozen)** | Frozen ($\Delta t=0.01$) | `0.008120` | `0.156886` | `0.592028` | `3.608077` | `25.149235` | 0.1456 | 0.3641 | **0.6934** |
| **HCTT-256 (Untrained)** | **Untrained** | `0.055885` | `0.325100` | `0.886675` | `3.304419` | `14.151903` | `0.0305` (`H=20`) | `0.0764` (`H=20`) | `0.1397` (masked) |

**Key Scientific Finding:** Frozen open-loop Phase 3.4 MLP/Ensemble rollouts compound drift rapidly over $0.20\text{ s}$ (`B3 = 25.15`, `B1 = 121.00`), whereas simple **Constant-Velocity ($\Delta t=0.01\text{ s}$)** achieves `0.075635` at `H=20`. Benchmarking HCTT-256 in Stage 4 against Constant-Velocity and Persistence ensures learned rollouts clear a physically meaningful bar rather than an inflated open-loop MLP baseline.

---

### 3. Peg-in-Hole Matched Physical-Time & Native-$\Delta t$ Reference

#### 3.1 Native $\Delta t = 0.01\text{ s}$ Reference Baselines (`val` Strict Windows, 50 Episodes)
Exact strict-window counts: `H=1` (`1,050`), `H=3` (`950`), `H=5` (`850`), `H=10` (`600`), `H=20` (`100`):

| Model ($\Delta t = 0.01\text{ s}$) | `0.01s` (`H=1`) MSE | `0.03s` (`H=3`) MSE | `0.05s` (`H=5`) MSE | `0.10s` (`H=10`) MSE | `0.20s` (`H=20`) MSE |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Persistence Baseline** | **`33.143792`** | **`51.725002`** | **`70.042198`** | **`113.143995`** | `128.561058` |
| **Constant-Velocity Baseline** | `91.384201` | `468.021180` | `1247.695032` | `5592.462518` | **`108.314691`** |
| **HCTT-256 (Untrained)** | `33.234935` | `52.401102` | `71.498632` | `117.527399` | `146.810987` |

*(Impulsive rim impacts and contact state discontinuities make linear Constant-Velocity extrapolate wildly at intermediate horizons `H=3..10`.)*

#### 3.2 Frozen Phase 3.4 Peg-in-Hole `B1–B3` at Native $\Delta t = 0.02\text{ s}$ (`n_rollouts = 440` per Horizon)
Evaluated strictly at native `dt=0.02s` on `PHASE3_SEED_POOLS['offline_test_peg_in_hole']` (`3080..3119`) and aligned by physical duration $T_{\text{phys}}$ (**incompatible at raw step index** with $\Delta t=0.01\text{ s}$):

| Frozen Architecture ($\Delta t = 0.02\text{ s}$) | `0.02s` (`k=1`, equiv `2@0.01`) | `0.06s` (`k=3`, equiv `6@0.01`) | `0.10s` (`k=5`, equiv `10@0.01`) | `0.20s` (`k=10`, equiv `20@0.01`) |
| :--- | :---: | :---: | :---: | :---: |
| **B1 (`M4_acmm`, Frozen $\Delta t=0.02\text{ s}$)** | `158.842181` | `502.024961` | `709.104705` | `2842.395016` |
| **B2 (`M4_acmm`, Frozen $\Delta t=0.02\text{ s}$)** | `47.752002` | `76.359341` | `130.616633` | `192.101448` |
| **B3 (`M4_acmm`, Frozen $\Delta t=0.02\text{ s}$)** | **`25.208478`** | **`19.053606`** | **`14.360045`** | **`113.168681`** |

Notice that at matching `0.10s` physical horizon (`k=5` at $\Delta t=0.02\text{ s}$), `B3` achieves `14.360045` in the native $\Delta t=0.02\text{ s}$ controller rollout regime vs Persistence `113.143995` in Stage 3.3 `val` ($\Delta t=0.01\text{ s}$), because Peg-in-Hole $\Delta t=0.02\text{ s}$ uses a distinct trajectory distribution (`Phase 3` controller) from Stage 3 `PegInHole`. Stage 4 will evaluate HCTT-256 on Stage 3.5 `final_test` directly against $\Delta t=0.01\text{ s}$ Persistence & Constant-Velocity references and clearly label Phase 3.4 Peg `B1–B3` as native-$\Delta t=0.02\text{ s}$ reference points.
