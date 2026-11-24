from pathlib import Path


def test_controller_does_not_read_the_oracle_or_true_friction():
    source = (Path(__file__).resolve().parent / "topology_aware_recovery_controller.py").read_text(encoding="utf-8")
    assert "oracle_protocol" not in source
    assert 'scenario["mu"]' not in source
    assert "extra_events" not in source
