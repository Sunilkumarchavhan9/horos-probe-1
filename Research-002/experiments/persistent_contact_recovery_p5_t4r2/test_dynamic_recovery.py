import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from p5_t4r2_sim_env import LifecycleGraspEnv
from topology_aware_recovery_controller import TopologyAwareRecoveryController


def test_topology_controller_emits_a_causal_label_and_respects_slew():
    scenario = None
    seed = 4891001
    while scenario is None and seed < 4891300:
        scenario = _draw(seed, 4, "train")
        seed += 1
    env = LifecycleGraspEnv(scenario, "Protocol_A", semantics="oneshot", finger="physical")
    obs = env.reset()
    ctrl = TopologyAwareRecoveryController(4, per_contact=10.0, seed=1)
    ctrl.reset(obs)
    labels = []
    for _ in range(scenario["horizon_steps"]):
        command = ctrl.step(obs)
        labels.append(command["decision_label"])
        obs, info = env.step(command)
    assert info["applied_slew_violations"] == 0
    assert info["applied_bound_violations"] == 0
    assert any(label in ("FEASIBLE", "INFEASIBLE", "UNCERTAIN", "DEGRADED_RECOVERY") for label in labels)
