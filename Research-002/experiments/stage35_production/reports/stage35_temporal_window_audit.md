# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 01: Temporal Window Feasibility & Path-Separation Audit

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-01-WINDOW-AUDIT`  
**Date:** 2026-11-25  

---

### 1. Executive Summary

Stage 3.4 audited the certified Stage 3.3 production dataset (`500` Compliant Prelift episodes at $T=20$, `500` Peg-in-Hole episodes at $T=30$) and discovered a structural feasibility gap: no Compliant Prelift ($T=20$) window possesses both a full 10-step observation history ($H_{\text{hist}} = 10$) and a full 20-step prediction horizon ($H_{\text{fut}} = 20$), because an unpadded anchor $t$ must satisfy $t \ge 9$ and $t \le T - H$. For $T=20, H=20$, that interval ($9 \le t \le 0$) is empty ($0$ windows).

Stage 3.5 resolves this ambiguity via two structural controls implemented in `src/world_model_vnext/dataset.py` and `src/world_model_vnext/evaluator.py`:
1. **Strict Path Separation:** Path 1 (`strict_unpadded`, accessed via `iter_strict_batches_for_horizon(horizon, ...)`) enforces `hist_mask.all() == True` (`valid_hist_len == 10`) and `future_mask[:, :horizon].all() == True`. Partial-history windows are strictly excluded or rejected with `ContractViolationError`. Path 2 (`masked_sliding`) retains boundary windows cleanly with explicit attention/loss masks and is **never merged** with Path 1 in evaluation metrics (`evaluate_strict_vs_masked`).
2. **Long-Horizon Compliant Prelift Generation ($T=40$):** An isolated 500-episode `prelift_long` dataset ($T=40$, $0.40\text{ s}$ at $\Delta t = 0.01\text{ s}$) yields **12 strict unpadded `(10, 20)` windows per episode** ($6,000$ total; $4,176$ in `train`), while preserving all 1,000 Stage 3.3 archives completely unmodified.

---

### 2. Recomputed Window Feasibility Matrix

Verified from `experiments/stage35_production/stage35_verification_metrics.json`:

| Sub-Dataset / Partition | Episodes | Length ($T$) | Horizon ($H$) | Path 2: Valid Horizon Windows (`masked_sliding`) | Path 1: Strict Unpadded (`full 10-hist + H`) | Anchors/Ep (Strict) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Stage 3.3 Prelift (Preserved)** | 500 | 20 | `H = 1` | 10,000 | 5,500 | 11 (`t=9..19`) |
| **Stage 3.3 Prelift (Preserved)** | 500 | 20 | `H = 3` | 9,000 | 4,500 | 9 (`t=9..17`) |
| **Stage 3.3 Prelift (Preserved)** | 500 | 20 | `H = 5` | 8,000 | 3,500 | 7 (`t=9..15`) |
| **Stage 3.3 Prelift (Preserved)** | 500 | 20 | `H = 10` | 5,500 | 1,000 | 2 (`t=9..10`) |
| **Stage 3.3 Prelift (Preserved)** | 500 | 20 | `H = 20` | 500 | **0** | **0** |
| **Stage 3.5 `prelift_long`** | 500 | 40 | `H = 1` | 20,000 | 15,500 | 31 (`t=9..39`) |
| **Stage 3.5 `prelift_long`** | 500 | 40 | `H = 3` | 19,000 | 14,500 | 29 (`t=9..37`) |
| **Stage 3.5 `prelift_long`** | 500 | 40 | `H = 5` | 18,000 | 13,500 | 27 (`t=9..35`) |
| **Stage 3.5 `prelift_long`** | 500 | 40 | `H = 10` | 15,500 | 11,000 | 22 (`t=9..30`) |
| **Stage 3.5 `prelift_long`** | 500 | 40 | `H = 20` | 10,500 | **6,000** | **12** (`t=9..20`) |
| **Stage 3.3 Peg-in-Hole** | 500 | 30 | `H = 1` | 15,000 | 10,500 | 21 (`t=9..29`) |
| **Stage 3.3 Peg-in-Hole** | 500 | 30 | `H = 3` | 14,000 | 9,500 | 19 (`t=9..27`) |
| **Stage 3.3 Peg-in-Hole** | 500 | 30 | `H = 5` | 13,000 | 8,500 | 17 (`t=9..25`) |
| **Stage 3.3 Peg-in-Hole** | 500 | 30 | `H = 10` | 10,500 | 6,000 | 12 (`t=9..20`) |
| **Stage 3.3 Peg-in-Hole** | 500 | 30 | `H = 20` | 5,500 | **1,000** | **2** (`t=9..10`) |

---

### 3. Stage 3.5 `train` & `val` Split Strict Counts

Using the clean `stage35_final_split_manifest.json` (`train`: 348 Prelift $T=40$ + 350 Peg-in-Hole $T=30$; `val`: 76 Prelift $T=40$ + 50 Peg-in-Hole $T=30$):

| Partition / Task | Episodes | `H = 1` Strict | `H = 3` Strict | `H = 5` Strict | `H = 10` Strict | `H = 20` Strict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`train` — `compliant_prelift` ($T=40$)** | 348 | 10,788 | 10,092 | 9,396 | 7,656 | 4,176 |
| **`train` — `peg_in_hole` ($T=30$)** | 350 | 7,350 | 6,650 | 5,950 | 4,200 | 700 |
| **`val` — `compliant_prelift` ($T=40$)** | 76 | 2,356 | 2,204 | 2,052 | 1,672 | 912 |
| **`val` — `peg_in_hole` ($T=30$)** | 50 | 1,050 | 950 | 850 | 600 | 100 |

---

### 4. Untrained HCTT Smoke Comparison: Path 1 Strict vs. Path 2 Masked (`val` Only)

Evaluated strictly under `torch.no_grad()` on `val` (`seed=3500`), leaving `final_test` untouched:

- **Compliant Prelift ($T=40$, 76 `val` episodes):**
  - **Path 2 History Distribution:** `valid_hist_len` 1..9 each have `76` partial-history windows (684 total), while `valid_hist_len == 10` has `2,356` strict windows (`3,040` total).
  - **State MSE (`Path 1 Strict` vs `Path 2 Masked`):**
    - `H=1`: `0.055885` ($n=2,356$) vs `0.105742` ($n=3,040$)
    - `H=3`: `0.325100` ($n=2,204$) vs `0.416581` ($n=2,888$)
    - `H=5`: `0.886675` ($n=2,052$) vs `0.974977` ($n=2,736$)
    - `H=10`: `3.304419` ($n=1,672$) vs `3.403053` ($n=2,356$)
    - `H=20`: `14.151903` ($n=912$) vs `14.331589` ($n=1,596$)
- **Peg-in-Hole ($T=30$, 50 `val` episodes):**
  - **Path 2 History Distribution:** `valid_hist_len` 1..9 each have `50` partial-history windows (450 total), while `valid_hist_len == 10` has `1,050` strict windows (`1,500` total).
  - **State MSE (`Path 1 Strict` vs `Path 2 Masked`):**
    - `H=1`: `33.234935` ($n=1,050$) vs `43.802747` ($n=1,500$)
    - `H=3`: `52.401102` ($n=950$) vs `58.000849` ($n=1,400$)
    - `H=5`: `71.498632` ($n=850$) vs `71.635777` ($n=1,300$)
    - `H=10`: `117.527399` ($n=600$) vs `118.152552` ($n=1,050$)
    - `H=20`: `146.810987` ($n=100$) vs `187.160304` ($n=550$)

Partial-history boundary windows materially shift `H=1` and `H=20` state MSE (`+89%` at `H=1` on Prelift and `+27.5%` at `H=20` on Peg-in-Hole), proving why Path 1 (`strict_unpadded`) and Path 2 (`masked_sliding`) must remain strictly separated across all Stage 4 protocols.
