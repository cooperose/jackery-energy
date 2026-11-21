"""Pure helper logic for Jackery Energy (no Home Assistant imports, unit-testable)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Power below this (W) is treated as noise / idle.
ACTIVE_W = 5

# Battery Pack 5000 Plus capacity (Wh) for expansion packs on the E5000 Plus.
PACK_CAPACITY_WH = 5040

KIND_POWER_STATION = "power_station"
KIND_TRANSFER_SWITCH = "transfer_switch"
KIND_UNKNOWN = "unknown"

# Cloud modelCode -> (slug, model name, rated capacity Wh)
# Codes cross-checked between the jlopez/socketry and porcupin26/private_jack projects.
MODEL_CODES: dict[int, tuple[str, str, int]] = {
    1: ("e3000pro", "Explorer 3000 Pro", 3024),
    2: ("e2000plus", "Explorer 2000 Plus", 2042),
    4: ("e300", "Explorer 300 Plus", 288),
    5: ("e1000plus", "Explorer 1000 Plus", 1264),
    13: ("e5000", "Explorer 5000 Plus", 5040),
    22: ("hp5000", "HomePower 5000 Plus", 5040),
}

# Raw Jackery property key -> readable id used in entity ids (sensor.jackery_e5000_battery)
FRIENDLY_KEYS: dict[str, str] = {
    "rb": "battery", "bt": "battery_temperature", "ip": "input_power", "op": "output_power",
    "acip": "ac_input_power", "cip": "dc_input_power", "acps": "ac_output_power",
    "acpsp": "ac_socket_power", "it": "time_to_full", "ot": "time_remaining",
    "acov": "ac_voltage", "acov1": "ac_voltage_l2", "acohz": "ac_frequency",
    "ec": "error_code", "ta": "temperature_alarm", "pal": "power_alarm", "wsig": "wifi_signal",
    "oac": "ac_output", "odc": "dc_output", "odcu": "usb_output", "odcc": "car_output",
    "iac": "ac_input_enabled", "idc": "dc_input_enabled", "ups": "ups_mode",
    "sfc": "super_charge", "wss": "wireless_charging",
}

POWER_SOURCES = ["solar", "grid", "grid_and_solar", "battery", "idle", "offline"]
BATTERY_STATES = ["idle", "charging", "discharging"]


@dataclass
class DeviceProfile:
    """What we know about a device after classification."""

    slug: str
    model: str
    capacity_wh: int | None
    kind: str


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def num(value: Any) -> float | None:
    """Coerce an API value to a number (or None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value))
    except ValueError:
        return None


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "device"


def classify(device: dict[str, Any]) -> DeviceProfile:
    """Work out what kind of Jackery device this is from the device-list entry."""
    name = device.get("devNickname") or device.get("devName") or ""
    haystack = " ".join(
        str(device.get(k) or "")
        for k in ("devNickname", "devName", "devModel", "productType", "modelName")
    ).lower()

    if (
        "transfer" in haystack
        or re.search(r"\bsts\b", haystack)
        or "ts02" in haystack
    ):
        return DeviceProfile("sts", "Smart Transfer Switch", None, KIND_TRANSFER_SWITCH)

    code = _to_int(device.get("modelCode"))
    if code in MODEL_CODES:
        slug, model, cap = MODEL_CODES[code]
        return DeviceProfile(slug, model, cap, KIND_POWER_STATION)

    if re.search(r"(?<!\d)5000(?!\d)", haystack):
        return DeviceProfile("e5000", "Explorer 5000 Plus", 5040, KIND_POWER_STATION)
    if re.search(r"(?<!\d)300(?!\d)", haystack):
        return DeviceProfile("e300", "Explorer 300 Plus", 288, KIND_POWER_STATION)

    model = str(device.get("devModel") or device.get("productType") or "Jackery device")
    return DeviceProfile(slugify(name or model), model, None, KIND_UNKNOWN)


def unique_slugs(profiles: list[DeviceProfile]) -> None:
    """Make slugs unique in place (second E300 becomes e300_2, etc.)."""
    seen: dict[str, int] = {}
    for p in profiles:
        if p.slug in seen:
            seen[p.slug] += 1
            p.slug = f"{p.slug}_{seen[p.slug]}"
        else:
            seen[p.slug] = 1


def power_source(solar: float | None, grid: float | None, output: float | None,
                 online: bool | None = True) -> str:
    """Summarise where power is coming from right now."""
    if online is False:
        return "offline"
    s = (solar or 0) > ACTIVE_W
    g = (grid or 0) > ACTIVE_W
    if s and g:
        return "grid_and_solar"
    if s:
        return "solar"
    if g:
        return "grid"
    if (output or 0) > ACTIVE_W:
        return "battery"
    return "idle"


def derive_device(props: dict[str, Any], capacity_wh: int | None,
                  packs: list[dict[str, Any]] | None = None,
                  online: bool | None = True) -> dict[str, Any]:
    """Compute derived values for a single power station."""
    ip = num(props.get("ip"))
    op = num(props.get("op"))
    acip = num(props.get("acip"))
    rb = num(props.get("rb"))
    bs = _to_int(props.get("bs"))

    grid = acip
    if ip is not None:
        solar = max(ip - (acip or 0), 0)
    else:
        solar = num(props.get("cip"))

    net = ip - op if ip is not None and op is not None else None

    if bs in (0, 1, 2):
        state = BATTERY_STATES[bs]
    elif net is None:
        state = None
    elif net > ACTIVE_W:
        state = "charging"
    elif net < -ACTIVE_W:
        state = "discharging"
    else:
        state = "idle"

    capacity = capacity_wh
    stored = rb / 100 * capacity_wh if rb is not None and capacity_wh else None
    pack_count = 0
    for pack in packs or []:
        prb = num(pack.get("rb"))
        if prb is None:
            continue
        pack_count += 1
        capacity = (capacity or 0) + PACK_CAPACITY_WH
        stored = (stored or 0) + prb / 100 * PACK_CAPACITY_WH

    return {
        "solar_power": solar,
        "grid_input_power": grid,
        "output_power": op,
        "battery_net_power": net,
        "battery_charge_power": max(net, 0) if net is not None else None,
        "battery_discharge_power": max(-net, 0) if net is not None else None,
        "battery_state": state,
        "stored_energy": round(stored, 1) if stored is not None else None,
        "total_capacity": capacity,
        "battery_pack_count": pack_count,
        "power_source": power_source(solar, grid, op, online),
    }


# Power stations that can feed the Smart Transfer Switch.
STS_FEEDERS = ("e5000", "hp5000")


def derive_system(devices: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate derived values across all devices.

    Each item: {"derived": {...}, "props": {...}, "online": bool, "kind": str, "slug": str}

    The Smart Transfer Switch reports power flowing to the backed-up house circuits
    (``op``). Whatever the 5000 Plus isn't supplying to it must be coming from the grid
    (pass-through), so that is counted as grid power and house load.
    """
    stations = [d for d in devices if d.get("kind") != KIND_TRANSFER_SWITCH]
    online = [d for d in stations if d.get("online") is not False and d.get("derived")]
    switches = [d for d in devices if d.get("kind") == KIND_TRANSFER_SWITCH
                and d.get("online") is not False and d.get("props")]

    def total(key: str, items: list[dict[str, Any]] = online) -> float | None:
        vals = [d["derived"].get(key) for d in items if d["derived"].get(key) is not None]
        return sum(vals) if vals else None

    circuits = None
    if switches:
        vals = [num(d["props"].get("op")) for d in switches]
        vals = [v for v in vals if v is not None]
        circuits = sum(vals) if vals else None

    passthrough = None
    if circuits is not None:
        feeders = [d for d in online if str(d.get("slug", "")).startswith(STS_FEEDERS)]
        fed = total("output_power", feeders) or 0
        passthrough = max(circuits - fed, 0)

    solar = total("solar_power")
    grid_charge = total("grid_input_power")
    station_out = total("output_power")
    net = total("battery_net_power")
    stored = total("stored_energy")
    capacity = total("total_capacity")

    grid = grid_charge
    load = station_out
    if passthrough is not None:
        grid = (grid or 0) + passthrough
        load = (load or 0) + passthrough

    if stored is not None and capacity:
        level = round(stored / capacity * 100, 1)
    else:
        rbs = [num(d["props"].get("rb")) for d in online]
        rbs = [r for r in rbs if r is not None]
        level = round(sum(rbs) / len(rbs), 1) if rbs else None

    # Backed-up house circuits: grid if the switch is passing grid power through.
    if passthrough is not None and passthrough > ACTIVE_W:
        source = "grid_and_solar" if (solar or 0) > ACTIVE_W else "grid"
    else:
        source = power_source(solar, grid_charge, station_out, True if online else False)

    return {
        "solar_power": solar,
        "grid_input_power": grid,
        "grid_charge_power": grid_charge,
        "grid_to_circuits_power": passthrough,
        "circuits_power": circuits,
        "load_power": load,
        "battery_net_power": net,
        "battery_charge_power": max(net, 0) if net is not None else None,
        "battery_discharge_power": max(-net, 0) if net is not None else None,
        "battery_level": level,
        "stored_energy": round(stored, 1) if stored is not None else None,
        "total_capacity": capacity,
        "devices_online": sum(1 for d in devices if d.get("online") is True),
        "devices_total": len(devices),
        "power_source": source,
    }


class EnergyAccumulator:
    """Trapezoidal integration of power (W) into energy (kWh)."""

    MAX_GAP_S = 600  # don't integrate across long outages / restarts

    def __init__(self, total_kwh: float = 0.0) -> None:
        self.total_kwh = total_kwh
        self._last_w: float | None = None
        self._last_ts: float | None = None

    def add(self, power_w: float | None, ts: float) -> float:
        if power_w is None or power_w < 0:
            self._last_w, self._last_ts = None, None
            return self.total_kwh
        if self._last_w is not None and self._last_ts is not None:
            dt = ts - self._last_ts
            if 0 < dt <= self.MAX_GAP_S:
                self.total_kwh += (self._last_w + power_w) / 2 * dt / 3_600_000
        self._last_w, self._last_ts = power_w, ts
        return self.total_kwh
