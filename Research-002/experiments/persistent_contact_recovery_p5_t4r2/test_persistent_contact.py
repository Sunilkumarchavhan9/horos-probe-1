import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from p5_t4r2_sim_env import LifecycleGraspEnv


def test_loaded_contact_persists_for_100_steps_without_another_event():
    scenario = None
    seed = 4890401
    while scenario is None and seed < 4890600:
        scenario = _draw(seed, 4, "train")
        seed += 1
    scenario["event"] = "sudden_invalidation"
    scenario["lost_ids"] = [0]
    scenario["event_time"] = 0.0
    scenario["extra_events"] = []
    scenario["mu"] = [1.2, 1.2, 1.2, 1.2]
    scenario["mass"] = 0.22
    scenario["inertia"] = 0.5 * 0.22 * 0.04 * 0.04
    scenario["disturbance_force"] = [0.0, 0.0]
    scenario["disturbance_torque"] = 0.0
    scenario["initial_velocity"] = [0.0, 0.0]
    scenario["initial_omega"] = 0.0
    scenario["support_available"] = False
    scenario["horizon_steps"] = 180
    env = LifecycleGraspEnv(scenario, "Protocol_A", semantics="oneshot", finger="physical")
    obs = env.reset()
    info = None
    for _ in range(180):
        cmd = [18.0 if v else 0.0 for v in obs["valid"]]
        obs, info = env.step({"cmd_fn": cmd, "recontact": [not v for v in obs["valid"]], "state": "RECOVERY"})
    assert info["formations"] == 1
    assert info["max_persist"] >= 100
    assert obs["valid"][0] is True
    assert info["loss_applications"] == 1
