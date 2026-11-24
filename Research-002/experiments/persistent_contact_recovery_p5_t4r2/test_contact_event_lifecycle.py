import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from p5_t4r2_sim_env import LifecycleGraspEnv


def _scenario():
    scenario = None
    seed = 4890101
    while scenario is None and seed < 4890300:
        scenario = _draw(seed, 4, "train")
        seed += 1
    scenario["event"] = "sudden_invalidation"
    scenario["lost_ids"] = [0]
    scenario["event_time"] = 0.0
    scenario["extra_events"] = []
    scenario["mu"] = [1.1, 1.1, 1.1, 1.1]
    scenario["mass"] = 0.25
    scenario["inertia"] = 0.5 * 0.25 * 0.04 * 0.04
    scenario["disturbance_force"] = [0.0, 0.0]
    scenario["disturbance_torque"] = 0.0
    scenario["support_available"] = False
    scenario["horizon_steps"] = 40
    return scenario


def test_repeat_scheduler_calls_loss_on_later_steps():
    env = LifecycleGraspEnv(_scenario(), "Protocol_A", semantics="repeat", finger="original")
    obs = env.reset()
    for _ in range(30):
        cmd = [18.0 if v else 0.0 for v in obs["valid"]]
        obs, info = env.step({"cmd_fn": cmd, "recontact": [False] * 4, "state": "RECOVERY"})
    assert info["loss_calls"] > 20


def test_oneshot_scheduler_consumes_the_loss_once():
    env = LifecycleGraspEnv(_scenario(), "Protocol_A", semantics="oneshot", finger="original")
    obs = env.reset()
    for _ in range(30):
        cmd = [18.0 if v else 0.0 for v in obs["valid"]]
        obs, info = env.step({"cmd_fn": cmd, "recontact": [False] * 4, "state": "RECOVERY"})
    assert info["loss_applications"] == 1
    assert info["events_consumed"] == 1
    assert obs["valid"][0] is False
