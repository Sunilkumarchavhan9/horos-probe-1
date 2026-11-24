# Contact lifecycle audit

`FeasibilityGraspEnv._event` applies `sudden_invalidation`, `multi_loss`, `stiffness_change`, and `object_motion` on every step with `t >= event_time`. `_lose` returns immediately once the contact is in the lost set. A recontact removes it from that set. The next step therefore calls `_lose` again.

The corrective finger model treats that new loss as a fresh arrival and sets the normal force back to 0. The loaded contact does not persist. The archived 5 mm / 0.40 s return rule is the original `_try_recontact`, not the corrective finger. The corrective finger uses a 10 mm retract and 0.18 m/s travel, then a 1.5 mm gap test.

Live comparison on the same held-still grasp:

| Semantics | Loss calls | Distinct applications | Formations | Final contact 0 valid | Final Fn |
|---|---:|---:|---:|---|---:|
| repeat | 40 | 10 | 1 | True | 0.0 |
| one-shot | 1 | 1 | 1 | True | 9.5 |

Repeat scheduling keeps invoking the loss. One-shot consumption applies it once, and the returned contact can then carry force. A persistent obstruction is a different event type and is still re-applied on purpose.
