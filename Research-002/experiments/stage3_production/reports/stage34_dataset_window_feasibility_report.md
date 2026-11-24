# HOROS PROBE-1 — Research-002
## Stage 3.4 Deliverable 06: Temporal Window & Horizon Feasibility Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.4 — HCTT-256 Implementation and Temporal Training-Pipeline Completion  
**Audited Dataset:** 1,000 Stage 3.3 Production Episodes (`500` Prelift, `500` Peg-in-Hole), $\Delta t = 0.01\text{ s}$  

---

### 1. Structural Horizon Constraint Audit

The locked HCTT specification targets:
- Historical observation window: $H_{\text{hist}} = 10$ timesteps ($100\text{ ms}$)
- Multi-step prediction horizons: $H \in \{1, 3, 5, 10, 20\}$ (maximum $H_{\text{fut}} = 20$, $200\text{ ms}$)

However, the certified Stage 3.3 production episodes have fixed lengths:
- **`compliant_prelift`:** $T = 20$ transitions ($200\text{ ms}$ total)
- **`peg_in_hole`:** $T = 30$ transitions ($300\text{ ms}$ total)

An anchor step $t$ requires $t \ge 9$ to accumulate 10 unpadded history observations (`0..9`) and $t \le T - H$ to contain $H$ unpadded future targets (`t..t+H-1`). Consequently:
- For **Compliant Prelift ($T=20$)** at $H=20$: $t \ge 9 \land t \le 0$ is **EMPTY** ($0$ unpadded windows exist).
- For **Peg-in-Hole ($T=30$)** at $H=20$: $t \in \{9, 10\}$ provides exactly **2 unpadded windows per episode** ($1,000$ across 500 episodes).

---

### 2. Measured Window Counts Across All 1,000 Episodes

Exact counts verified by `HCTTTemporalDataset.feasibility_summary()` (`stage34_pretraining_eval_metrics.json`):

| Task ($T$) | Horizon ($H$) | `masked_sliding` Valid Horizon Windows | Full 10-Step History + Horizon (Unpadded) | `strict_unpadded (min_fut=1)` | `strict_unpadded (min_fut=20)` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `compliant_prelift` ($T=20$) | **H = 1** | 10,000 | 5,500 | 5,500 | 0 |
| `compliant_prelift` ($T=20$) | **H = 3** | 9,000 | 4,500 | 4,500 | 0 |
| `compliant_prelift` ($T=20$) | **H = 5** | 8,000 | 3,500 | 3,500 | 0 |
| `compliant_prelift` ($T=20$) | **H = 10** | 5,500 | 1,000 | 1,000 | 0 |
| `compliant_prelift` ($T=20$) | **H = 20** | **500** ($t=0$, 1-step hist) | **0** | **0** | **0** |
| `peg_in_hole` ($T=30$) | **H = 1** | 15,000 | 10,500 | 10,500 | 1,000 |
| `peg_in_hole` ($T=30$) | **H = 3** | 14,000 | 9,500 | 9,500 | 1,000 |
| `peg_in_hole` ($T=30$) | **H = 5** | 13,000 | 8,500 | 8,500 | 1,000 |
| `peg_in_hole` ($T=30$) | **H = 10** | 10,500 | 6,000 | 6,000 | 1,000 |
| `peg_in_hole` ($T=30$) | **H = 20** | **5,500** | **1,000** ($t \in \{9, 10\}$) | **1,000** | **1,000** |

---

### 3. Justified Padding & Masking Policy

Because dropping incomplete windows eliminates 100% of Prelift `H=20` evaluations while silent zero-padding corrupts self-attention and teaches models to predict zero forces past episode termination, Stage 3.4 enforces an **Explicit Masked Sliding-Window Policy (`masked_sliding`)**:

1. **History Left-Masking (`hist_mask`):** When $t < 9$, missing early steps are zero-filled on the left and marked `hist_mask = False`. Transformer self-attention sets scores for `hist_mask == False` keys to `-1e9` before softmax, and history attention-pooling excludes them completely. `hist_mask[:, -1]` (anchor state $s_t$) is always `True`.
2. **Future Right-Masking (`future_mask`):** For $k \ge T - t$, missing future steps are marked `future_mask = False` and `target_modes = -100`. Causal attention masks them out, and `HCTTMultiObjectiveLoss` + `HCTTReadOnlyEvaluator` compute losses/metrics **strictly over valid unpadded entries** (`future_mask == True`).
3. **Evaluation Reporting Rule:** Horizon-20 metrics on `compliant_prelift` ($n=50$ windows across 50 test episodes, all anchored at $t=0$ with 1-step initial history) must always report exact sample size (`n_valid_windows = 50`) alongside `H_10` ($n=550$, full 10-step history available on $n=100$) so $H=20$ generalization on Prelift is never overclaimed. Long-horizon ($H=20$ with full 10-step history) Stage 4 collection should generate $T \ge 35\text{–}50$ Prelift trajectories.
