import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from p5_t4r2_sim_env import LifecycleGraspEnv


def test_commands_above_the_cap_are_clipped_and_slew_limited():
    scenario = None
    seed = 4891401
    while scenario is None and seed < 4891600:
        scenario = _draw(seed, 4, "train")
        seed += 1
    env = LifecycleGraspEnv(scenario, "Protocol_B", semantics="oneshot", finger="physical")
    obs = env.reset()
    for _ in range(25):
        obs, info = env.step({"cmd_fn": [80.0] * 4, "recontact": [False] * 4, "state": "RECOVERY"})
    assert info["applied_bound_violations"] == 0
    assert info["applied_slew_violations"] == 0
    assert info["applied_budget_violations"] == 0
    assert max(obs["fn_applied"]) <= 25.0 + 1e-6
