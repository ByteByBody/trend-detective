"""Project-wide constants and defaults.

Values mirror the Global Constraints in the implementation plan.
"""

#: Default variable studied by the cry-wolf slice.
DEFAULT_VARIABLE = "soil_moisture_rootzone"

#: Benjamini-Hochberg false-discovery-rate level.
FDR_Q = 0.05

#: Minimum fraction of valid (non-NaN) months required to test a pixel.
MIN_VALID_FRACTION = 0.8

#: Minimum connected-patch size (pixels) kept by the coherence gate.
MIN_PATCH_SIZE = 5

#: Default analysis grid shape as (ny, nx).
DEFAULT_GRID = (45, 90)

#: Default number of monthly timesteps.
DEFAULT_MONTHS = 240

#: Smaller grid for the interactive demo (keeps the API responsive).
DEMO_GRID = (30, 60)

#: AR(1) autocorrelation of the demo grid — enough memory to be a fair test.
DEMO_AUTOCORR = 0.5

#: Data source used by the API when none is configured.
DATA_SOURCE = "synthetic"

#: Live NASA POWER source: bounding box as (lat_min, lat_max, lon_min, lon_max).
#: Default is a Greenland box (cryosphere, long MERRA-2 record).
POWER_BBOX = (60.0, 70.0, -50.0, -40.0)

#: First and last year requested from POWER.
POWER_START = 1990
POWER_END = 2024

#: Per-request timeout for POWER, in seconds (multi-tile boxes add requests).
POWER_TIMEOUT = 120.0
