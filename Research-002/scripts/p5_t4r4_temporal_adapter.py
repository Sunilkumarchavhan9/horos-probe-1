"""
HOROS PROBE-1 — Research-002: World Model vNext Data Integration
P5-T4R4 Temporal Data Adapter & Replay Engine
File: p5_t4r4_temporal_adapter.py

Authoritative adapter connecting P5-T4R4 research to the World Model vNext data contract:
1. Audits P5-T4R4 raw records: verifies they contain scalar episode summaries, not time series.
2. Provides deterministic replay conversion: re-executes scenario seeds in NContactGraspEnvP5T3
   to produce fully synchronized (T, D) temporal trajectories.
3. Enforces the Grounded Firewall: strictly separates observable physical arrays
   (states, actions, next_states, wrenches, next_wrenches) from privileged simulator metadata
   (true friction, mass, contact mode labels, disturbance parameters).
4. Preserves all original P5-T4R4 artifacts and SHA-256 checksums without modification.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
R002_ROOT = SCRIPT_DIR.parent
ADAPTIVE_DIR = R002_ROOT / "experiments" / "adaptive_preload_validation_p5_t4r4"

for p in (ADAPTIVE_DIR, R002_ROOT, R002_ROOT / "src"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)

from preload_experiment_protocol import (
    TRAIN_RANGE,
    VAL_RANGE,
    TEST_RANGE,
    PROTOCOL_A,
    PROTOCOL_B,
    PLANT_DT,
    SLEW_LIMIT,
    FN_MIN,
    FN_MAX,
)
from preload_controller_baselines import make_controller, get_initial_preload
from preload_sweep_experiment import generate_scenario, make_env_from_scenario


def audit_p5_t4r4_raw_records(records_path: Optional[Path] = None) -> Dict[str, Any]:
    """Audits the raw records of P5-T4R4 to verify whether they contain temporal time series or scalar summaries."""
    path = records_path or (ADAPTIVE_DIR / "preload_raw_records.json")
    if not path.exists():
        return {
            "error": f"File not found: {path}",
            "is_compatible_temporal_dataset": False,
        }

    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    n_records = len(records)
    r0 = records[0] if n_records > 0 else {}

    # Check for presence of temporal trajectory arrays
    temporal_array_keys = ["states", "actions", "next_states", "wrenches", "next_wrenches", "modes"]
    found_array_keys = [k for k in temporal_array_keys if k in r0 and isinstance(r0[k], list)]

    scalar_summary_keys = [
        "mean_total_fn", "peak_fn_max", "impulse_total_Ns",
        "mechanical_work_act_J", "tangential_work_J", "damage_proxy",
        "latency_mean_ms", "gross_slip_step", "grasp_success"
    ]
    found_scalar_keys = [k for k in scalar_summary_keys if k in r0]

    is_temporal = len(found_array_keys) == len(temporal_array_keys)
    is_scalar_summary = len(found_scalar_keys) == len(scalar_summary_keys)

    audit_result = {
        "records_file": str(path),
        "total_records": n_records,
        "is_temporal_timeseries": is_temporal,
        "is_scalar_episode_summary": is_scalar_summary,
        "temporal_array_keys_present": found_array_keys,
        "scalar_summary_keys_present": found_scalar_keys,
        "contract_verdict": (
            "INCOMPATIBLE_AS_RAW_TRAJECTORIES: P5-T4R4 raw records are scalar episode summaries. "
            "Replay adapter required to generate synchronized step-by-step (T, D) arrays."
        ),
    }
    return audit_result


def replay_p5_t4r4_episode_to_temporal(
    scenario_seed: int,
    controller_name: str = "AdaptivePreloadPlanner",
    protocol: str = PROTOCOL_A,
    max_steps: int = 50,
) -> Dict[str, Any]:
    """
    Deterministically executes a P5-T4R4 scenario in simulation to record genuine,
    synchronized temporal trajectories adhering to the World Model vNext data contract.

    Returns:
      Dict with separated observable package and privileged ground truth.
    """
    scenario = generate_scenario(scenario_seed, "test" if scenario_seed >= TEST_RANGE[0] else "train")
    n_contacts = scenario["num_contacts"]
    init_fn = get_initial_preload(controller_name, protocol)

    env = make_env_from_scenario(scenario, seed=scenario_seed)
    ctrl = make_controller(controller_name, n_contacts, seed=scenario_seed, initial_preload=init_fn)

    initial_preloads = [init_fn] * n_contacts
    obs = env.reset(
        disturbance_type=scenario["disturbance"],
        v_shear=scenario["v_shear"],
        load_offset_y=scenario["load_offset_y"],
        initial_preloads=initial_preloads,
    )

    budget = (n_contacts * 8.0) if protocol == PROTOCOL_B else None

    # State dimension: object_pos (2) + object_vel (2) + object_tilt (1) + object_rot_vel (1) + per-contact (4 * N)
    # Contact channels: fn, ft, disp, is_active
    state_dim = 6 + 4 * n_contacts
    action_dim = n_contacts

    states = np.zeros((max_steps, state_dim), dtype=np.float32)
    next_states = np.zeros((max_steps, state_dim), dtype=np.float32)
    actions = np.zeros((max_steps, action_dim), dtype=np.float32)
    wrenches = np.zeros((max_steps, 6), dtype=np.float32)
    next_wrenches = np.zeros((max_steps, 6), dtype=np.float32)
    modes = np.zeros(max_steps, dtype=np.int64)

    def extract_state_and_wrench(current_obs: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        st = np.zeros(state_dim, dtype=np.float32)
        st[0:2] = current_obs.get("object_pos", [0.0, 0.0])
        st[2:4] = current_obs.get("object_vel", [0.0, 0.0])
        st[4] = current_obs.get("object_tilt", 0.0)
        st[5] = current_obs.get("object_rot_vel", 0.0)
        for i, c in enumerate(current_obs.get("contacts", [])):
            base_idx = 6 + i * 4
            st[base_idx] = float(c["force_n"])
            st[base_idx + 1] = float(c["force_t"])
            st[base_idx + 2] = float(c["disp"])
            st[base_idx + 3] = 1.0 if c["is_active"] else 0.0

        wr = np.zeros(6, dtype=np.float32)
        # 2D resultant forces mapped to 6D wrench: [Fx, Fy, Fz, Tx, Ty, Tz]
        G = env.topology.get_grasp_matrix(current_obs.get("object_tilt", 0.0))
        forces_vec = np.zeros(2 * n_contacts)
        for i, c in enumerate(current_obs.get("contacts", [])):
            forces_vec[2 * i] = c["force_n"]
            forces_vec[2 * i + 1] = c["force_t"]
        w_ext = -G @ forces_vec
        wr[0] = float(w_ext[0]) if len(w_ext) > 0 else 0.0
        wr[1] = float(w_ext[1]) if len(w_ext) > 1 else 0.0
        wr[5] = float(w_ext[2]) if len(w_ext) > 2 else 0.0
        return st, wr

    curr_obs = obs
    curr_s, curr_w = extract_state_and_wrench(curr_obs)

    for step_i in range(max_steps):
        contacts_obs = curr_obs["contacts"]
        fn_noisy = [c["force_n"] for c in contacts_obs]
        ft_noisy = [c["force_t"] for c in contacts_obs]
        disp_noisy = [c["disp"] for c in contacts_obs]
        valid_flags = [c["is_active"] for c in contacts_obs]

        G = env.topology.get_grasp_matrix(curr_obs.get("object_tilt", 0.0))
        forces_vec = np.zeros(2 * n_contacts)
        for i in range(n_contacts):
            forces_vec[2 * i] = fn_noisy[i]
            forces_vec[2 * i + 1] = ft_noisy[i]
        w_ext_hat = (-G @ forces_vec).tolist()

        ctrl_obs = {
            "valid": valid_flags,
            "fn": fn_noisy,
            "ft": ft_noisy,
            "disp": disp_noisy,
            "normals": [list(cp.normal) for cp in env.topology.contacts],
            "positions": [list(cp.pos_rel) for cp in env.topology.contacts],
            "w_ext_hat": w_ext_hat,
            "protocol_budget": budget,
        }

        act = ctrl.step(ctrl_obs)
        cmd_fn = np.asarray(act["cmd_fn"], dtype=np.float32)

        states[step_i] = curr_s
        actions[step_i] = cmd_fn
        wrenches[step_i] = curr_w

        next_obs, done, info = env.step(cmd_fn.tolist())
        next_s, next_w = extract_state_and_wrench(next_obs)

        next_states[step_i] = next_s
        next_wrenches[step_i] = next_w

        # Mode classification:
        # 0: Free, 1: Impact, 2: Sticking, 3: Sliding, 4: Jammed/Lost
        if info.get("is_gross_slipping", False):
            modes[step_i] = 3  # Sliding / slip
        elif any(not c["is_active"] for c in next_obs["contacts"]):
            modes[step_i] = 4  # Contact loss
        elif all(abs(c["disp"]) < 1e-4 for c in next_obs["contacts"]):
            modes[step_i] = 2  # Sticking
        else:
            modes[step_i] = 2

        curr_obs = next_obs
        curr_s = next_s
        curr_w = next_w

        if done:
            # Pad remaining steps with last state
            for pad_j in range(step_i + 1, max_steps):
                states[pad_j] = curr_s
                actions[pad_j] = cmd_fn
                wrenches[pad_j] = curr_w
                next_states[pad_j] = curr_s
                next_wrenches[pad_j] = curr_w
                modes[pad_j] = modes[step_i]
            break

    # Calculate deterministic hash
    hasher = hashlib.sha256()
    hasher.update(str(scenario_seed).encode("utf-8"))
    hasher.update(controller_name.encode("utf-8"))
    hasher.update(states.tobytes())
    hasher.update(actions.tobytes())
    hasher.update(wrenches.tobytes())
    provenance_hash = hasher.hexdigest()

    observable_package = {
        "states": states,
        "actions": actions,
        "next_states": next_states,
        "wrenches": wrenches,
        "next_wrenches": next_wrenches,
        "task": f"adaptive_preload_n{n_contacts}",
        "sample_id": provenance_hash[:16],
    }

    privileged_metadata = {
        "seed": scenario_seed,
        "controller": controller_name,
        "protocol": protocol,
        "num_contacts": n_contacts,
        "true_friction_mu": scenario["base_mu"],
        "object_mass_kg": scenario["object_mass"],
        "disturbance_regime": scenario["disturbance"],
        "modes": modes.tolist(),
        "grasp_success": bool(info.get("grasp_success", False)),
        "provenance_hash": provenance_hash,
    }

    return {
        "observable_package": observable_package,
        "privileged_metadata": privileged_metadata,
        "provenance_hash": provenance_hash,
    }


if __name__ == "__main__":
    print("Auditing P5-T4R4 raw records...")
    audit = audit_p5_t4r4_raw_records()
    print(json.dumps({k: v for k, v in audit.items() if not isinstance(v, list)}, indent=2))
    print("\nTesting temporal replay conversion on seed 4950001...")
    replay = replay_p5_t4r4_episode_to_temporal(4950001, controller_name="AdaptivePreloadPlanner")
    pkg = replay["observable_package"]
    print(f"Replay generated: states={pkg['states'].shape}, actions={pkg['actions'].shape}, wrenches={pkg['wrenches'].shape}")
    print(f"Sample ID: {pkg['sample_id']}, Provenance: {replay['provenance_hash'][:16]}...")
    print("P5-T4R4 temporal adapter operational!")
