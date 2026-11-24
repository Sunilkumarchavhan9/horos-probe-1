# P5-T3 N=3 contact-loss proof, checked against the solver

This note uses the contact normals, tangents, and grasp matrix in `n_contact_generalization_p5_t3/contact_topology.py`. It does not change that file.

## Geometry

Symmetric radial contacts at 0°, 120°, and 240°. Contact 0 is removed. The remaining object-frame vectors from `ContactTopology.create_symmetric_radial` are:

| Contact | Angle | Normal (inward) | Tangent |
|---|---|---|---|
| 1 | 120° | `[0.5, -√3/2]` | `[-√3/2, -0.5]` |
| 2 | 240° | `[0.5, +√3/2]` | `[+√3/2, -0.5]` |

The coded force on the object is `f = fn * n - ft * t`. The published P5-T3 algebra writes both positive tangential forces as if they reduce `Fx`. That sign is true for contact 2 and false for contact 1. The capacity statement is unchanged once each tangential force is allowed to take the opposing sign: the largest opposing horizontal force has magnitude `(√3/2)(μ1 fn1 + μ2 fn2)`.

The grasp-matrix torque row for both normal-force columns is 0. The torque row for both tangential columns is `-R` with `R = 0.04 m`. Zero torque therefore requires `ft1 = -ft2` in the coded sign convention, and zero `Fy` then requires `fn1 = fn2`. Under those constraints the horizontal balance condition is exactly

\[
\mu \ge \frac{1}{\sqrt{3}} \approx 0.57735
\]

when the two friction coefficients are equal. With the stratum values `μ1 = 0.53` and `μ2 = 0.56`, both are below that threshold. The equal-force horizontal margin is negative:

\[
\frac{\sqrt{3}}{2}(\mu_1+\mu_2) - 1 \approx -0.056
\]

The smallest coded `Fx` for equal forces in `[3, 25]` N, choosing the opposing tangential signs, is `0.168 N` at `3 N` per contact. No equal positive preload cancels `Fx`. The linear program agrees: with `fn >= 3` and zero external wrench the problem is infeasible, and with gravity on a `0.5 kg` object it is also infeasible. With `fn >= 0` and zero external wrench the only solution is the zero force, which is not a grasp.

## What that does not prove

These are different statements.

| Claim | Status against the archived episodes and the solver |
|---|---|
| The stated horizontal equilibrium condition fails for `μ < 1/√3` | Supported |
| No full wrench balance exists for positive equal preloads | Supported. `Fy` and torque make equal forces necessary, and those forces cannot cancel `Fx` |
| The object must accelerate at `>= 6 m/s²` | Not supported. That figure is the undamped normal imbalance `3 N / 0.5 kg` with friction ignored. With cone-limited friction the net `Fx` is about `0.056 fn`, so `0.17 N` at the `3 N` floor |
| The object crosses the `25 mm` drop boundary in under `90 ms` | Not supported. The learned-controller raw records for the 20 N=3 loss episodes have displacements from `3.06 mm` to `10.94 mm`. None reach `25 mm`. Tilt stays below `20°`. All 20 are flagged `is_gross_slipping` |
| The simulator integrates the undamped Newton equation used in the prose | Not supported. `ObjectWrenchDynamics` applies `10 N·s/m` of linear damping |

The N=3 recovery rate of 0/20 is real. The mechanism in the archived episodes is gross slip of the remaining contacts, not a measured crossing of the 25 mm boundary.
