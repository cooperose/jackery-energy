"""Constants for Jackery Energy."""

DOMAIN = "jackery_energy"
MANUFACTURER = "Jackery"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 10

# Battery-pack list is polled less often than live properties.
PACK_POLL_EVERY = 10

SYSTEM_SLUG = "system"

CONF_CIRCUIT_INTERVAL = "circuit_interval"
DEFAULT_CIRCUIT_INTERVAL = 10  # minutes; 0 = only when you press Refresh

# Backup-only mode length used by the "Charge from grid" button (minutes).
GRID_CHARGE_MINUTES = 1440

WORK_MODES = {0: "automatic_charging", 1: "charging_plan", 2: "self_powered"}
CIRCUIT_PRIORITIES = {0: "must_have", 1: "nice_to_have", 2: "non_priority"}
