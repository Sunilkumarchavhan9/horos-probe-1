# HOROS PROBE-1: Uncertainty-Aware Safe Adaptation for Multifingered Grasping Under Jointly Changing Contact Conditions

[![arXiv](https://img.shields.io/badge/arXiv-cs.RO%20%7C%20eess.SY-B31B1B.svg)](https://arxiv.org/abs/XXXX.XXXXX)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](LICENSE)
[![Verification](https://img.shields.io/badge/LaTeX-100%25%20Verified-brightgreen.svg)](arxiv_submission/check_latex.py)

Official open-source repository for **HOROS PROBE-1**, a predictive safety filter and adaptive uncertainty calibration framework for multi-fingered robotic grasping under nonstationary contact physics.

---

## 🔬 Scientific Overview

Safe robotic manipulation of fragile, dynamic objects remains a fundamental challenge in embodied intelligence. While foundation Vision-Language-Action (VLA) policies excel at high-level semantic generalization, direct motor execution leaves them vulnerable to sub-step contact nonstationarities, transport delays, and sudden friction drops.

**HOROS PROBE-1** solves this by establishing a certified, real-time predictive safety envelope:
1. **Action-Dependent Contact Sensitivity Mapping:** Derives forward contact force mappings $\mathbf{\Lambda}_i(\mathbf{q}_k)$ via linearized Hunt-Crossley elastomeric contact dynamics.
2. **Predictive Discrete-Time Control Barrier Function (p-CBF):** Formulates an 8-sided polyhedral friction cone ($) with radial conservatism $\le 7.61\%$, fragile upper load limits ({\max} = 18.0\text{ N}$), minimum preload thresholds ({\min} = 1.0\text{ N}$), and spatial wrench balance $\mathbf{G} \mathbf{f}_c = \mathbf{w}_{\text{req}}$.
3. **Adaptive Conformal Inference (ACI):** Tracks non-exchangeable, nonstationary friction variations online at \text{ Hz}$, ensuring asymptotic marginal coverage without requiring exchangeability or asserting unverified deterministic certificates.

---

## 📊 Key Benchmark Findings (6,200 Evaluation Episodes)

Evaluated across **6,200 simulated manipulation episodes** in Isaac Lab with PhysX 5 ( = 100$ independent seeds per condition, 50-step episodes):

| Controller | Regime A (Nominal) | Regime B (Liquid Influx) | Regime C (Sloshing CoM) | Regime D (Coupled Stress) | Crush Damage Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **B1: Fixed-Force Clamping** | 0.0% | 0.0% | 0.0% | **100.0%** (Drop) | 0.0% |
| **B2: Reactive Grip Reflex** | 0.0% | 0.0% | 0.0% | **100.0%** (Drop) | 0.0% |
| **B3: Unconstrained Flow VLA** | 0.0% | 0.0% | 0.0% | **100.0%** (Drop) | 0.0% |
| **B4: Certainty-Equivalence MPC** | 0.0% | 0.0% | 0.0% | **100.0%** (Drop) | 0.0% |
| **HOROS PROBE-1 (Ours)** | **0.0%** | **0.0%** | **0.0%** | **0.0%** (Drop) | **0.0%** (Crush) |

- **Coupled Nonstationarity (Regime D):** Combining pouring fluid, sloshing center-of-mass, and a 60% friction collapse, all baselines suffer a **100.0% drop rate**, while HOROS PROBE-1 achieves **0.0% drops and 0.0% crush damage** (paired McNemar test $\chi^2 = 98.01, p < 10^{-20}$).
- **Real-Time Execution Feasibility:** Median OSQP solver latency is **0.193 ms**; maximum observed cycle latency is **9.603 ms**, remaining strictly inside the **10.0 ms (100 Hz)** watchdog budget.

---

## 📁 Repository Structure

`	ext
horos-probe-1/
├── README.md                           # Main repository overview and documentation
├── 00-research-objective.md            # Research Dossier 00 through 20
├── ...                                 # (Complete 21-document scientific trajectory)
├── 20-peer-review-and-submission-dossier.md
├── arxiv_submission/                   # Ready-to-upload LaTeX manuscript & bundles
│   ├── main.tex                        # Primary LaTeX document
│   ├── references.bib                  # Synchronized peer-reviewed bibliography (19 citations)
│   ├── check_latex.py                  # Automated 100% LaTeX integrity verifier
│   ├── generate_paper_figures.py       # Publication figure generation engine
│   ├── probe1_arxiv_source.zip         # Clean 19-file upload package for arXiv autocompiler
│   ├── probe1_arxiv_submission.zip     # Complete 31-file reproducibility archive
│   ├── sections/                       # 10 modular LaTeX sections
│   ├── figures/                        # 7 vector PDFs and PNG previews
│   └── data/                           # Raw evaluation JSON ledgers (6,200 episodes)
├── src/                                # Core Python implementation
│   ├── contact/                        # Hunt-Crossley & elastomeric contact dynamics
│   ├── control/                        # OSQP predictive safety filter & decision supervisor
│   ├── estimation/                     # Recursive load invariant & friction estimators
│   └── safety/                         # Polyhedral cone barriers & conformal calibration
├── simulation/                         # TacSL / Isaac Lab simulation harness
├── experiments/                        # Full evaluation runner & benchmark suite (EXP-001 to 006)
├── baselines/                          # Fixed-force, reflex squeeze, unconstrained, CE-MPC baselines
├── datasets/                           # Groot teleop G1 dataset loaders
├── manifests/                          # Experiment execution manifests
├── reproducibility/                    # Dockerfile and frozen environment specifications
└── tests/                              # Pytest test suite
`

---

## 🚀 Quickstart

### 1. Installation
`ash
git clone https://github.com/Sunilkumarchavhan9/horos-probe-1.git
cd horos-probe-1
pip install -r reproducibility/requirements-freeze.txt
`

### 2. Run Test Suite
`ash
pytest tests/ -v
`

### 3. Verify LaTeX Manuscript & Figures
`ash
python arxiv_submission/check_latex.py
python arxiv_submission/generate_paper_figures.py
`

### 4. Reproduce Benchmark Evaluations
`ash
python experiments/run_full_evaluations.py
`

---

## 📄 arXiv Manuscript & Preprints

The complete paper source is located in rxiv_submission/:
- **Primary Category:** cs.RO (Robotics)
- **Secondary Category:** eess.SY (Systems and Control)
- **Compilation Package:** Upload rxiv_submission/probe1_arxiv_source.zip directly to [arXiv.org/submit](https://arxiv.org/submit).

### Citation
`ibtex
@article{chavhan2026horosprobe1,
  title={Uncertainty-Aware Safe Adaptation for Multifingered Grasping Under Jointly Changing Contact Conditions},
  author={Chavhan, Sunil Kumar},
  journal={arXiv preprint arXiv:2611.XXXXX [cs.RO]},
  year={2026}
}
`

---

## 📬 Contact & Correspondence
- **Lead Author:** Sunil Kumar Chavhan
- **Organization:** HOROS Physical Intelligence Research Group
- **Email:** sunil@horoslab.io
- **GitHub:** [https://github.com/Sunilkumarchavhan9/horos-probe-1](https://github.com/Sunilkumarchavhan9/horos-probe-1)
