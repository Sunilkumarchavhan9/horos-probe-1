"""Physics checks for the isolated P5-T4 plant and wrench balance."""

import numpy as np

from contact_feasibility_solver import reaction_friction, solve_bounded_wrench
from contact_wrench_analysis import (
    MU_CRIT_SYMMETRIC_PAIR,
    external_wrench,
    make_radial_contact,
    symmetric_pair_horizontal_margin,
    ObjectState,
)


def _radial(n, mu, mass=0.4):
    com = np.zeros(2)
    contacts = [make_radial_contact(i, 2.0 * np.pi * i / n, 0.04, mu, 4.0e4, com) for i in range(n)]
    obj = ObjectState(mass=mass, radius=0.04)
    return contacts, external_wrench(obj)


def test_three_four_and_five_contact_gravity_balance():
    for n in (3, 4, 5):
        contacts, wrench = _radial(n, 0.9)
        solved = solve_bounded_wrench(contacts, wrench, spread_load=True)
        assert solved["feasible"], n
        assert np.max(np.abs(solved["residual"])) < 1e-4


def test_symmetric_pair_below_mu_crit_has_no_positive_balance():
    contacts, _ = _radial(3, 0.55)
    contacts[0].valid = False
    contacts[1].mu = 0.53
    contacts[2].mu = 0.56
    assert symmetric_pair_horizontal_margin(0.53, 0.56, 8.0, 8.0) < 0.0
    blocked = solve_bounded_wrench(contacts, np.zeros(3), fn_min=3.0, fn_max=25.0)
    assert not blocked["feasible"]
    assert 0.53 < MU_CRIT_SYMMETRIC_PAIR and 0.56 < MU_CRIT_SYMMETRIC_PAIR


def test_external_force_and_torque_are_accounted():
    contacts, wrench = _radial(4, 0.85, mass=0.35)
    wrench = wrench + np.array([1.5, 0.2, 0.01])
    solved = solve_bounded_wrench(contacts, wrench)
    assert solved["feasible"]
    reaction = reaction_friction(contacts, solved["fn"], wrench)
    assert reaction["balanced"]


def test_removing_contacts_can_make_the_wrench_infeasible():
    contacts, wrench = _radial(3, 0.25, mass=0.6)
    for contact in contacts:
        contact.mu = 0.2
    contacts[0].valid = False
    contacts[1].valid = False
    solved = solve_bounded_wrench(contacts, wrench + np.array([6.0, 0.0, 0.0]))
    assert not solved["feasible"]
