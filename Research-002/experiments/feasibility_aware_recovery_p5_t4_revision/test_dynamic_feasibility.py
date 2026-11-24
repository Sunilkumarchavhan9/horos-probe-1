import _paths  # noqa: F401
import numpy as np

from contact_wrench_analysis import make_radial_contact
from dynamic_feasibility_solver import classify_dynamic


def test_four_feasibility_terms_are_separate_fields():
    contacts = [make_radial_contact(i, angle, 0.04, 0.9, 4.0e4, np.zeros(2)) for i, angle in enumerate((0.0, 2.1, 4.2))]
    report = classify_dynamic(
        contacts,
        fn_now=[1.0, 1.0, 1.0],
        w_ext=[0.0, -3.0, 0.0],
        position=[0.0, 0.02],
        velocity=[0.0, -0.4],
        theta=0.0,
        omega=0.0,
        mass=0.35,
        inertia=2.8e-4,
        dt=0.005,
        total_budget=None,
        target=[8.0, 8.0, 8.0],
    )
    keys = ("static_feasibility", "rate_limited_feasibility", "dynamic_recoverability", "robust_dynamic_feasibility")
    assert all(key in report for key in keys)
    assert isinstance(report["static_feasibility"], bool)
    assert isinstance(report["dynamic_recoverability"], bool)
    assert report["static_feasibility"] or report["dynamic_recoverability"] in (True, False)
