from contact_geometry_validator import formation_stage


def test_reaching_the_point_without_force_is_not_a_loaded_contact():
    assert formation_stage(0.02, 0.0, 0.0, 0.18) == "approaching"
    assert formation_stage(0.001, 0.0, 0.0, 0.18) == "forming"
    assert formation_stage(0.001, 0.0, 1.0, 0.18) == "loaded"
    assert formation_stage(0.001, 0.5, 1.0, 0.18) == "rejected_approach_speed"
