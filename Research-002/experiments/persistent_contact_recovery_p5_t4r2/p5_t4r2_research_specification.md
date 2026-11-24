# P5-T4R2 specification

This continues the P5-T4 corrective line. It does not open P5-T5. Archived verdicts stay fixed: P5-T3 CONDITIONAL SCIENTIFIC PASS, P5-T4 FAIL, P5-T4 corrective continuation FAIL.

Hardware is prohibited. The plant timestep is the archived 0.005 s. The slew limit is 50 N/s, so the largest force change is 0.25 N per step. The force clip remains [0, 25] N. A 3 N floor is not added, because the archived plant does not have one.

## Event semantics

- One-shot loss (`sudden_invalidation`, `multi_loss`, `object_motion`, `stiffness_change`): the loss is applied once. A later recontact is not removed by the same event.
- Persistent obstruction: the loss is re-applied until `obstruction_end`. A finger arrival does not clear it.
- A second one-shot loss is a different event id and a later timestamp. Restoring the first contact does not cancel it.
- Friction collapse and gradual friction change are not contact removals.

Preregistered scenario edits, applied before oracle labeling:

- If `seed % 23 == 0` and the draw is `sudden_invalidation`, the event becomes `persistent_obstruction` until the horizon ends.
- If `seed % 19 == 0`, the topology has at least 4 contacts, and the event removes contacts, one additional contact is given its own one-shot loss at 0.22 s.

## Selection

Train-only choice between `topology_10` and `topology_14` newtons per valid contact, allocated by the contact normal that opposes the estimated load. The winner is the higher Protocol A feasible-recovery rate on the one-shot physical plant. Ties break toward lower mean force. `preload_25` is a baseline, not a candidate.

The primary comparison baseline is chosen on the validation split from P5-T3, the archived P5-T4 controller, and `preload_25`, using Protocol A feasible recovery on the same plant. The held-out manifest is written before either choice is scored.

## Acceptance

The original full bars remain: infeasible recall ≥ 95%, false-feasible rate ≤ 5%, pooled feasible recovery ≥ 80%, each of N=3/4/5 ≥ 70%, safe termination ≥ 90%, zero force, slew, budget, and unexplained conservation violations, no causal leakage, P95 latency ≤ 1 ms, and exact replay. A PASS also requires a paired feasible-recovery gain over the validation-chosen baseline and over `preload_25` after a Bonferroni correction across those comparisons. Fixing the event scheduler is reported separately and is not itself a PASS.

Seeds: train 4890001–4892500, validation 4900001–4902500, held-out 4910001–4916000, stress 4920001–4921500.
