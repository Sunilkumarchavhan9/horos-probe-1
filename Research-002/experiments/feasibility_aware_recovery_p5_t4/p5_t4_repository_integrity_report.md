# P5-T4 repository integrity

**Canonical HEAD:** `a7bd9be2`  
**Date:** 2026-11-24  
**P5-T4 code location:** `research/PROBE-1/Research-002/experiments/feasibility_aware_recovery_p5_t4/` only

## Protected trees

`git status --short` on the following paths was empty after the P5-T4 work:

- `experiments/n_contact_generalization_p5_t3`
- `experiments/adaptive_preload_p5_t1`
- `experiments/multi_finger_cooperative_p5_t2`
- `experiments/contact_physics_v2`
- `experiments/stage3_production_readiness`

The P5-T3 regression files `test_p5_t3_physics.py`, `test_p5_t3_causality.py`, `test_p5_t3_contact_recovery.py`, and `test_p5_t3_controllers.py` were run after the new work and passed, 17 tests. The new P5-T4 tests passed, 15 tests. The historical P5-T3 scientific report was not edited.

Production seed registries were not written.

## Mirror

`C:\projects\horos-probe-1` exists and its HEAD is `8c1aab8`, which is not `a7bd9be2`. The P5-T3 experiment path used by this audit is not present in that mirror tree. Canonical/mirror parity does **not** hold for this milestone. Nothing was copied to the mirror.

## Git blob hashes of the closeout and result files

| File | Blob |
|---|---|
| `p5_t3_evidence_audit_addendum.md` | `ce97bc1415a2fe88a316ed7403339da1019f72df` |
| `p5_t3_protocol_b_revalidation.json` | `17fbdf6ee384e7d4ffbe5216bffc49089c6f6b76` |
| `p5_t3_statistical_revalidation.json` | `a2d3692de67248442fabe50c0777e99953adc821` |
| `p5_t3_actuator_safety_audit.json` | `c1bd52fd35bd62a4bfd694bf8d8355bc886bc93c` |
| `p5_t3_force_closure_proof.md` | `32a37eed29174ab02ceef1051683d51368c4253e` |
| `p5_t4_research_specification.md` | `1893ad442e878a74dda03b607ccb1298dfbdf623` |
| `p5_t4_evaluation_summary.json` | `820d72b6aba3b0da6356d216970cb5d9363ab50d` |
| `p5_t4_scientific_evaluation_report.md` | `1f7d474e106fa3d591efa13ef70e0e97fa734cd1` |
| `p5_t4_frozen_configuration.json` | `97bf104088396294198afc141754a10798a3af1d` |
| `p5_t4_feasibility_analysis.json` | `102ab754c8148a3791aa10a369ff2072c00fd54d` |
| `p5_t4_failure_analysis.json` | `f0d0a2247ebae75093643a7b8fb4b8c943b0a9cd` |
| `p5_t4_seed_registry.md` | `9fb065c4920556392bf19265951f51d45adc2e52` |

These hashes are `git hash-object` blob ids of the working-tree files at the time of the evaluation. The package is committed on `feat/probe1-stage33-production-readiness` in `horos-probe-1` only.
