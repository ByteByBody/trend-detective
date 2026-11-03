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
