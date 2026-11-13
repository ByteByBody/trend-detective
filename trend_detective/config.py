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

#: Step-change detector: months on each side of the split, the gap between
#: candidate splits, the field-relative cut in robust sigmas
#: (``median + STEP_THRESHOLD_SIGMA * 1.4826 * MAD``), and the band the
#: end-of-record shift must sit in relative to one window step: a durable step
#: lands near 1, a smooth ramp far above it, a returning excursion near 0.
STEP_WINDOW = 60
STEP_STRIDE = 6
STEP_THRESHOLD_SIGMA = 15.0
STEP_PERSISTENCE_MIN = 0.5
STEP_PERSISTENCE_MAX = 2.0

#: Default analysis grid shape as (ny, nx).
DEFAULT_GRID = (45, 90)

#: Default number of monthly timesteps.
DEFAULT_MONTHS = 240

#: Smaller grid for the interactive demo (keeps the API responsive).
DEMO_GRID = (30, 60)

#: AR(1) autocorrelation of the demo grid — enough memory to be a fair test.
DEMO_AUTOCORR = 0.5

#: Data source used by the API when none is configured. PSL is the only source
#: that can fill a world map; the first run downloads its chunks (see
#: PSL_CHUNK_YEARS) and every run after that is local.
DATA_SOURCE = "psl"

#: Live NASA POWER source: bounding box as (lat_min, lat_max, lon_min, lon_max).
#: Default is a Greenland box (cryosphere, long MERRA-2 record).
POWER_BBOX = (60.0, 70.0, -50.0, -40.0)

#: First and last year requested from POWER.
POWER_START = 1990
POWER_END = 2024

#: Per-request timeout for POWER, in seconds (multi-tile boxes add requests).
POWER_TIMEOUT = 120.0

#: Global NOAA PSL source: year range and spatial decimation (1 = full 2.5 deg).
#: Measured cost per analysis, 540 months: stride 1 -> ~140 s, stride 2 -> ~43 s.
PSL_START = 1980
PSL_END = 2024
PSL_STRIDE = 2

#: Years per NCSS request. THREDDS cut off a whole 45-year request at ~7 min with
#: a truncated body; shorter requests complete and each one is cached separately.
PSL_CHUNK_YEARS = 5
