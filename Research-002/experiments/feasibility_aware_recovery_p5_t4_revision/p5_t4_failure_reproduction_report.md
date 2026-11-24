# P5-T4 failure reproduction

The archived Protocol A records were recomputed without changing them. Every check against `p5_t4_evaluation_summary.json` matched: True.

| Metric | Recomputed | Archived summary |
|---|---:|---:|
| Infeasible recall | 0.9976 | 0.9976 |
| False-feasible rate | 0.0000 | 0.0000 |
| Feasible recovery | 0.1006 | 0.1006 |
| P5-T3 feasible recovery | 0.3732 | 0.3732 |
| Safe termination | 0.9504 | 0.9504 |
| Mean total force | 4.5191 N | 4.5191 N |
| P95 latency | 5.3304 ms | 5.3304 ms |

P5-T3 mean total force on the same episodes is 47.303 N. On feasible episodes the archived controller used 6.187 N when it recovered and 7.019 N when it did not. Failed feasible episodes have median peak displacement 227.4 mm.

The low force is the static program's minimum balance, followed by an unload when displacement passes 20 mm. It is not evidence that 4.5 N is the force required to hold the object. The comparison squeeze holds more often because it raises normal force, and therefore friction capacity, at 50 N/s. Raising the force limit is not available: the cap stays 25 N and the slew stays 50 N/s.

The archived plant steps at 5 ms, so the slew limit is 0.25 N per step. A 1 ms step is not the plant that produced these records. A trajectory sample of feasible held-out episodes was replayed on that original plant and stored in `p5_t4_recovery_failure_analysis.json`.

Decision counts: {'FEASIBLE': 352, 'INFEASIBLE': 426, 'UNCERTAIN': 6, 'DEGRADED_RECOVERY': 114, None: 2}.
