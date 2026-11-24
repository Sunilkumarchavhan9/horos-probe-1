"""
Preregistered P5-T4R4 Protocol & Constants.
Preload sweep and adaptive preload policy validation.
"""

from __future__ import annotations

PROTOCOL_A = "Protocol_A"
PROTOCOL_B = "Protocol_B"

# Fresh, non-overlapping research seeds:
# P4-T4B: 4400001–4400300
# P5-T2: 4600001–4600300
# P5-T3: 4710001–4710900
# P5-T4: 4810001–4840600
# P5-T4 Rev: 4850001–4881501
# P5-T4R2: 4890001–4921501
# P5-T4R4 (Current): 4930001–4961500
TRAIN_RANGE = (4930001, 4930301)       # 300 scenarios
VAL_RANGE = (4940001, 4940301)         # 300 scenarios
TEST_RANGE = (4950001, 4950601)        # 600 scenarios (held-out)
STRESS_RANGE = (4960001, 4960061)      # 60 scenarios

SWEEP_TARGETS = [3.0, 5.0, 8.0, 12.0, 16.0, 20.0, 25.0]
FRICTION_LEVELS = [0.35, 0.55, 0.75]
TOPOLOGIES = [3, 4, 5]

PLANT_DT = 0.001        # 1000 Hz physics simulation loop
CONTROL_DT = 0.005      # 200 Hz control loop
SLEW_LIMIT = 50.0       # 50.0 N/s max actuator rate limit (0.05 N/ms)
FN_MIN = 3.0            # 3.0 N minimum normal force clamp
FN_MAX = 25.0           # 25.0 N maximum normal force clamp
BUDGET_PER_CONTACT = 8.0  # Protocol B budget: sum(Fn) <= N_active * 8.0 N

# Micro-probe parameters for pre-contact friction estimation
PROBE_AMPLITUDE_M = 0.0003   # 0.3 mm micro-displacement probe
PROBE_VELOCITY_MPS = 0.0005  # 0.5 mm/s safe probing velocity
PROBE_DURATION_S = 0.030     # 30 ms probing window
PROBE_FN_NOMINAL = 4.0       # 4.0 N safe contact load during probe

# Noise parameters (calibrated from sensor specs)
SENSOR_NOISE_FORCE = 0.02    # 20 mN force noise
SENSOR_NOISE_DISP = 0.5e-6   # 0.5 um displacement noise
SENSOR_NOISE_TILT = 0.001    # 1 mrad tilt noise
