import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from p5_t4r2_sim_env import LifecycleGraspEnv
from topology_aware_recovery_controller import TopologyAwareRecoveryController


def _held(seed: int):
    scenario = _draw(seed, 4, "train")
    env = LifecycleGraspEnv(scenario, "Protocol_A", semantics="oneshot", finger="physical")
    obs = env.reset()
    ctrl = TopologyAwareRecoveryController(4, per_contact=10.0, seed=seed)
    ctrl.reset(obs)
    info = None
    for _ in range(scenario["horizon_steps"]):
        obs, info = env.step(ctrl.step(obs))
    return bool(info["grasp_held"]), round(info["displacement_m"], 8)


def test_same_seed_replays_the_same_grasp_bit():
    first = _held(4891701)
    second = _held(4891701)
    assert first == second
