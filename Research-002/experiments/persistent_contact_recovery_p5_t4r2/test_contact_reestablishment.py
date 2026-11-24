import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from p5_t4r2_sim_env import LifecycleGraspEnv


def test_obstruction_rejects_a_returning_finger():
    scenario = None
    seed = 4890701
    while scenario is None and seed < 4890900:
        scenario = _draw(seed, 4, "train")
        seed += 1
    scenario["event"] = "persistent_obstruction"
    scenario["lost_ids"] = [0]
    scenario["event_time"] = 0.0
    scenario["extra_events"] = []
    scenario["horizon_steps"] = 80
    scenario["support_available"] = False
    env = LifecycleGraspEnv(scenario, "Protocol_A", semantics="oneshot", finger="physical")
    obs = env.reset()
    for _ in range(80):
        cmd = [16.0 if v else 0.0 for v in obs["valid"]]
        obs, _info = env.step({"cmd_fn": cmd, "recontact": [True, False, False, False], "state": "RECOVERY"})
    assert obs["valid"][0] is False
    assert env.manager.formations == 0
