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
    # Smart Transfer Switch
    "ac1_bi": "port1_connected", "ac2_bi": "port2_connected",
    "ac1_rb": "port1_battery", "ac1_ip": "port1_input_power", "ac1_op": "port1_output_power",
    "ac1_ot": "port1_time_remaining", "ac1_bs": "port1_battery_state",
    "ac2_rb": "port2_battery", "ac2_ip": "port2_input_power", "ac2_op": "port2_output_power",
    "dg": "grid_energy_counter", "dh": "home_energy_counter", "ds": "solar_energy_counter",
    "de": "battery_energy_counter",
    "fz_ol": "overload", "fz_ta1": "temperature_alarm_1", "fz_ta2": "temperature_alarm_2",
    "fz_ec1": "error_code_1", "fz_ec2": "error_code_2", "fz_gs": "grid_status_code",
    "fz_es": "error_status", "fz_bs1": "battery_status_1", "fz_bs2": "battery_status_2",
    "fz_ntc": "ntc_fault", "fz_rtc": "clock_fault",
}

# Never expose these as entities (serial numbers, timezone offset).
HIDDEN_KEYS = {"ac1_sn", "ac2_sn", "sn", "uo", "mac", "wip", "wname", "ac1_bp", "ac2_bp"}

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

    code = _to_int(device.get("modelCode"))
    if (
        code == 2001
        or "hto785" in haystack
        or "transfer" in haystack
        or re.search(r"\bsts\b", haystack)
        or "ts02" in haystack
    ):
        return DeviceProfile("sts", "Smart Transfer Switch", None, KIND_TRANSFER_SWITCH)

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


# Below this, the transfer switch's in/out difference is measurement noise.
STS_GRID_MIN_W = 30


def sts_grid_to_station(sts_props: dict[str, Any] | None) -> float:
    """Grid power the Smart Transfer Switch is sending into its power station.

    The switch measures grid power in (``ip``) and power out to the house circuits (``op``);
    the difference is what is charging the 5000 Plus. Charging through the switch does not
    show up in the station's own ``acip``.
    """
    if not sts_props:
        return 0
    ip, op = num(sts_props.get("ip")), num(sts_props.get("op"))
    if ip is None or op is None:
        return 0
    g = ip - op
    return g if g >= STS_GRID_MIN_W else 0


def derive_device(props: dict[str, Any], capacity_wh: int | None,
                  packs: list[dict[str, Any]] | None = None,
                  online: bool | None = True, extra_grid: float = 0) -> dict[str, Any]:
    """Compute derived values for a single power station.

    ``extra_grid``: grid power reaching this station through a transfer switch.
    """
    ip = num(props.get("ip"))
    op = num(props.get("op"))
    acip = num(props.get("acip"))
    rb = num(props.get("rb"))
    bs = _to_int(props.get("bs"))

    grid = (acip or 0) + extra_grid if (acip is not None or extra_grid) else None
    if ip is not None:
        solar = max(ip - (grid or 0), 0)
    else:
        solar = num(props.get("cip"))

    net = ip - op if ip is not None and op is not None else None

    # Actual power flow first; the device's own "bs" flag often stays 0 while charging.
    if net is not None and net > ACTIVE_W:
        state = "charging"
    elif net is not None and net < -ACTIVE_W:
        state = "discharging"
    elif bs in (1, 2):
        state = BATTERY_STATES[bs]
    elif net is not None or bs == 0:
        state = "idle"
    else:
        state = None

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

    drain = max(-net, 0) if net is not None else None
    runtime = (
        round(stored / drain, 2)
        if stored is not None and drain is not None and drain > ACTIVE_W else None
    )

    return {
        "runtime": runtime,
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

    # Home backup group (5000 Plus + transfer switch + its solar): if the grid failed now,
    # how long would the 5000 Plus carry the circuits? load - solar, from stored energy.
    feeders_all = [d for d in online if str(d.get("slug", "")).startswith(STS_FEEDERS)]
    home_stored = total("stored_energy", feeders_all)
    home_solar = total("solar_power", feeders_all) or 0
    home_load = max(circuits or 0, total("output_power", feeders_all) or 0) if feeders_all else None
    home_drain = (home_load - home_solar) if home_load is not None else None
    home_runtime = (
        round(home_stored / home_drain, 2)
        if home_stored is not None and home_drain is not None and home_drain > ACTIVE_W else None
    )

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
        "home_backup_load": home_load,
        "home_backup_drain": max(home_drain, 0) if home_drain is not None else None,
        "home_backup_stored": home_stored,
        "home_backup_runtime": home_runtime,
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
