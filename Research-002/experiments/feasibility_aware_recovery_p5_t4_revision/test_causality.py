from pathlib import Path


def test_controller_source_does_not_read_oracle_or_true_friction():
    root = Path(__file__).resolve().parent
    names = (
        "predictive_recovery_controller.py",
        "dynamic_force_allocator.py",
        "recovery_trajectory_optimizer.py",
        "dynamic_feasibility_solver.py",
        "contact_reestablishment_planner.py",
        "rate_limited_dynamics.py",
    )
    for name in names:
        source = (root / name).read_text(encoding="utf-8")
        assert "oracle_protocol" not in source
        assert 'scenario["mu"]' not in source
        assert "scenario['mu']" not in source
