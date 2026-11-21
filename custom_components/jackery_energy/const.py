"""Constants for Jackery Energy."""

DOMAIN = "jackery_energy"
MANUFACTURER = "Jackery"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15

# Battery-pack list is polled less often than live properties.
PACK_POLL_EVERY = 10

SYSTEM_SLUG = "system"
