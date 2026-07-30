# kicadstamp/constants.py

# --- Fields and roles ---
ROLE_FIELD_NAME = "Role"
# The second custom schematic field, alongside Role — physical instance/cluster
# (NOT native KiCad Group — deliberately, see chat discussion: we don't want to
# share data space with someone else's, still-unfinished multichannel
# mechanics in KiCad). Hierarchy ("Channel_1/1V2_PLL_PI_FILTER") is supported
# for free via segment-prefix comparison — a flat name without "/"
# simply degenerates to the special case of exact match.
CLUSTER_FIELD_NAME = "Cluster"

# --- Tolerances ---
POSITION_TOLERANCE_NM = 10_000       # 0.01 mm
ANGLE_TOLERANCE_DEG = 0.1
POSITION_TOLERANCE_MM = 0.01

# --- Default parameters ---
DEFAULT_BATCH_SIZE = 10
DEFAULT_TIMEOUT_MS = 20000
DEFAULT_LOG_DIR = "logs"

# --- Registry ---
SPOKE_LEVEL_ROLE_PLACEHOLDER = "__spoke__"