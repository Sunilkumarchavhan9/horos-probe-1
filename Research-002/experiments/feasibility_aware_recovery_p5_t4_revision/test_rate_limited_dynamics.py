import _paths  # noqa: F401
import numpy as np

from rate_limited_dynamics import slew_toward


def test_slew_respects_fifty_newtons_per_second():
    fn = np.array([1.0, 2.0, 0.0])
    target = np.array([25.0, 0.0, 25.0])
    valid = [True, True, False]
    nxt = slew_toward(fn, target, valid, dt=0.005)
    assert abs(nxt[0] - 1.25) < 1e-9
    assert abs(nxt[1] - 1.75) < 1e-9
    assert nxt[2] == 0.0
    assert np.max(np.abs(nxt - fn)) <= 50.0 * 0.005 + 1e-9
