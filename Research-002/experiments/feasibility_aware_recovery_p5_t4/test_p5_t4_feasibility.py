"""Feasibility classes stay distinct, and an optimizer status is not trusted without a residual check."""

import numpy as np

from contact_feasibility_solver import classify_five, solve_bounded_wrench, two_contact_mu_crit_case
from contact_wrench_analysis import external_wrench, make_radial_contact, ObjectState


def test_five_feasibility_terms_are_separate_fields():
    com = np.zeros(2)
    contacts = [make_radial_contact(i, 2.0 * np.pi * i / 4, 0.04, 0.8, 4.0e4, com) for i in range(4)]
    obj = ObjectState(mass=0.35, radius=0.04)
    report = classify_five(contacts, external_wrench(obj), fn_now=[4, 4, 4, 4], sigma=[0.02] * 4, total_budget=None, speed=0.0)
    assert report["geometric_force_closure"]
    assert report["wrench_feasibility"]
    assert report["bounded_actuator_feasibility"]
    assert "robust_feasibility" in report and "dynamic_recoverability" in report


def test_residual_rejection_blocks_a_false_certificate():
    com = np.zeros(2)
    contacts = [make_radial_contact(0, -np.pi / 2, 0.04, 0.05, 4.0e4, com)]
    solved = solve_bounded_wrench(contacts, np.array([8.0, -1.0, 0.2]))
    assert not solved["feasible"]
    if solved["residual"] is not None:
        assert np.max(np.abs(solved["residual"])) > 1e-4 or solved["reason"] != "verified_equilibrium"


def test_mu_crit_case_matches_the_solver_sign_convention():
    proof = two_contact_mu_crit_case()
    assert proof["horizontal_margin_at_8N"] < 0.0
    assert not proof["bounded_with_fn_at_least_3N"]["feasible"]
    assert not proof["bounded_under_0.5kg_gravity"]["feasible"]
    # Zero external wrench is balanced by zero force. That is not a grasp.
    assert proof["bounded_with_fn_nonnegative_and_zero_external"]["feasible"]
    assert proof["bounded_with_fn_nonnegative_and_zero_external"]["sum_fn"] == 0.0
