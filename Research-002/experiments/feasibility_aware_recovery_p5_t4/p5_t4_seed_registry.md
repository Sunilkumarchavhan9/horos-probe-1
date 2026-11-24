# P5-T4 seed registry

Allocated after reading the production registry and the P5-T1, P5-T2, and P5-T3 research ranges. None of those ranges are reused.

| Partition | Seeds | Role |
|---|---|---|
| Training | 4810001–4810899 | Residual selection only |
| Validation | 4820001–4820899 | Cutoff selection and primary-baseline selection |
| Held-out | 4830001–4833999 | Frozen before cutoff selection. Not used to choose a model |
| Stress | 4840001–4840599 | Unseen geometries, reported separately from the scorecard |

Not used, and not overlapped:

- Production and reserved production seeds at `3300000+` and `3390000+`
- P4 research seeds at `4300000+` and `4400000+`
- P5-T1 `4500001–4500300`
- P5-T2 `4600001–4600300`
- P5-T3 `4700001–4700900`, `4710001–4710900`, `4720001–4720300`

A seed is consumed even when the draw is rejected because the intact grasp has no equilibrium. Rejected seeds are not relabeled. If a feasible or infeasible quota cannot be filled inside the range, the shortfall file records the missing count. The realized scenario list is `p5_t4_split_shortfall.json` and `p5_t4_heldout_manifest_public.json` after the run.

Each accepted scenario stores its seed, topology, object state, contact angles, hidden friction and stiffness, mass, inertia, disturbance, loss event, sensor-noise settings, and the oracle's Protocol A and Protocol B labels. The controller does not read the oracle labels.
