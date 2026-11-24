# P5-T4R2 repository audit

`horos-probe-1` branch `feat/probe1-stage33-production-readiness` is at commit `512d6d1`. That commit is the corrective continuation and was not rewritten. The package is not on horoslab. P5-T3, original P5-T4, and the corrective directory were inspected and left unchanged.

The corrective Protocol A summary matches the reported table: feasible recovery 37.63%, matched P5-T3 38.06%, paired difference -0.43 points, p = 0.50, infeasible recall 98.16%, false-feasible rate 0.23%, safe termination 99.54%, P95 latency 0.051 ms. Topology rates are N=3 12.8%, N=4 39.8%, N=5 59.4%.

The archived plant timestep is 0.005 s. The slew limit is 50 N/s, which is 0.25 N per step. The force clip is [0, 25] N. Seeds 4810001–4840599 and 4850001–4881501 are already used. P5-T4R2 starts at 4890001.

The corrective failure is not only the scheduler. `preload_25` already used the actuator cap and still tied P5-T3. N=3 recovery stayed near 13%. The scheduler defect is an additional, separate fault and is measured on its own in the ablation.
