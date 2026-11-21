"""Sensors for Jackery Energy."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    MATCH_ALL,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .binary_sensor import BINARY_PROP_KEYS
from .const import DOMAIN, MANUFACTURER
from .coordinator import JackeryCoordinator, JackeryDevice
from .entity import JackeryDeviceEntity, JackerySystemEntity
from .logic import BATTERY_STATES, FRIENDLY_KEYS, POWER_SOURCES, EnergyAccumulator, num

M = SensorStateClass.MEASUREMENT
W = UnitOfPower.WATT


@dataclass(frozen=True, kw_only=True)
class JackerySensorDescription(SensorEntityDescription):
    scale: float | None = None
    zero_is_none: bool = False  # Jackery reports 0 for "not applicable" on time estimates


def _power(key: str, name: str, **kw: Any) -> JackerySensorDescription:
    return JackerySensorDescription(
        key=key, name=name, native_unit_of_measurement=W,
        device_class=SensorDeviceClass.POWER, state_class=M, **kw,
    )


def _energy(key: str, name: str) -> JackerySensorDescription:
    return JackerySensorDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
    )


_DIAG = EntityCategory.DIAGNOSTIC

# --- raw device properties (keys from the Jackery cloud /device/property call)
PROP_SENSORS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(key="rb", name="Battery", native_unit_of_measurement=PERCENTAGE,
                             device_class=SensorDeviceClass.BATTERY, state_class=M),
    JackerySensorDescription(key="bt", name="Battery temperature", scale=10,
                             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                             device_class=SensorDeviceClass.TEMPERATURE, state_class=M),
    _power("ip", "Input power"),
    _power("op", "Output power"),
    _power("acip", "AC input power"),
    _power("cip", "DC input power"),
    _power("acps", "AC output power"),
    _power("acpsp", "AC socket power"),
    JackerySensorDescription(key="it", name="Time to full", scale=10, zero_is_none=True,
                             native_unit_of_measurement=UnitOfTime.HOURS,
                             device_class=SensorDeviceClass.DURATION, state_class=M),
    JackerySensorDescription(key="ot", name="Time remaining", scale=10, zero_is_none=True,
                             native_unit_of_measurement=UnitOfTime.HOURS,
                             device_class=SensorDeviceClass.DURATION, state_class=M),
    JackerySensorDescription(key="acov", name="AC output voltage", scale=10,
                             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                             device_class=SensorDeviceClass.VOLTAGE, state_class=M),
    JackerySensorDescription(key="acov1", name="AC output voltage L2", scale=10,
                             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                             device_class=SensorDeviceClass.VOLTAGE, state_class=M),
    JackerySensorDescription(key="acohz", name="AC output frequency",
                             native_unit_of_measurement=UnitOfFrequency.HERTZ,
                             device_class=SensorDeviceClass.FREQUENCY, state_class=M),
    JackerySensorDescription(key="ec", name="Error code", entity_category=_DIAG),
    JackerySensorDescription(key="ta", name="Temperature alarm", entity_category=_DIAG),
    JackerySensorDescription(key="pal", name="Power alarm", entity_category=_DIAG),
    JackerySensorDescription(key="wsig", name="Wi-Fi signal", entity_category=_DIAG,
                             state_class=M),
)

# --- computed per power station
DERIVED_SENSORS: tuple[JackerySensorDescription, ...] = (
    _power("solar_power", "Solar power", icon="mdi:solar-power"),
    _power("grid_input_power", "Grid input power", icon="mdi:transmission-tower-import"),
    _power("battery_net_power", "Battery net power", icon="mdi:battery-sync"),
    _power("battery_charge_power", "Battery charge power", icon="mdi:battery-arrow-up"),
    _power("battery_discharge_power", "Battery discharge power", icon="mdi:battery-arrow-down"),
    JackerySensorDescription(key="stored_energy", name="Stored energy",
                             native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                             device_class=SensorDeviceClass.ENERGY_STORAGE, state_class=M),
    JackerySensorDescription(key="total_capacity", name="Total capacity", entity_category=_DIAG,
                             native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                             device_class=SensorDeviceClass.ENERGY_STORAGE),
    JackerySensorDescription(key="battery_pack_count", name="Expansion battery packs",
                             entity_category=_DIAG, icon="mdi:battery-plus"),
    JackerySensorDescription(key="battery_state", name="Battery state", translation_key="battery_state",
                             device_class=SensorDeviceClass.ENUM, options=BATTERY_STATES),
    JackerySensorDescription(key="power_source", name="Power source", translation_key="power_source",
                             device_class=SensorDeviceClass.ENUM, options=POWER_SOURCES,
                             icon="mdi:transit-connection-variant"),
)

DEVICE_ENERGY: tuple[tuple[JackerySensorDescription, str], ...] = (
    (_energy("solar_energy", "Solar energy"), "solar_power"),
    (_energy("grid_input_energy", "Grid input energy"), "grid_input_power"),
    (_energy("output_energy", "Output energy"), "output_power"),
    (_energy("battery_charge_energy", "Battery charge energy"), "battery_charge_power"),
    (_energy("battery_discharge_energy", "Battery discharge energy"), "battery_discharge_power"),
)

SYSTEM_SENSORS: tuple[JackerySensorDescription, ...] = (
    _power("solar_power", "Solar power", icon="mdi:solar-power"),
    _power("grid_input_power", "Grid input power", icon="mdi:transmission-tower-import"),
    _power("load_power", "Load power", icon="mdi:home-lightning-bolt"),
    _power("battery_net_power", "Battery net power", icon="mdi:battery-sync"),
    _power("battery_charge_power", "Battery charge power", icon="mdi:battery-arrow-up"),
    _power("battery_discharge_power", "Battery discharge power", icon="mdi:battery-arrow-down"),
    JackerySensorDescription(key="battery_level", name="Battery level", native_unit_of_measurement=PERCENTAGE,
                             device_class=SensorDeviceClass.BATTERY, state_class=M),
    JackerySensorDescription(key="stored_energy", name="Stored energy",
                             native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                             device_class=SensorDeviceClass.ENERGY_STORAGE, state_class=M),
    JackerySensorDescription(key="total_capacity", name="Total capacity",
                             native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                             device_class=SensorDeviceClass.ENERGY_STORAGE),
    JackerySensorDescription(key="devices_online", name="Devices online", icon="mdi:lan-connect",
                             state_class=M),
    JackerySensorDescription(key="power_source", name="Power source", translation_key="power_source",
                             device_class=SensorDeviceClass.ENUM, options=POWER_SOURCES,
                             icon="mdi:transit-connection-variant"),
)

SYSTEM_ENERGY: tuple[tuple[JackerySensorDescription, str], ...] = (
    (_energy("solar_energy", "Solar energy"), "solar_power"),
    (_energy("grid_input_energy", "Grid input energy"), "grid_input_power"),
    (_energy("load_energy", "Load energy"), "load_power"),
    (_energy("battery_charge_energy", "Battery charge energy"), "battery_charge_power"),
    (_energy("battery_discharge_energy", "Battery discharge energy"), "battery_discharge_power"),
)

PACK_SENSORS: tuple[JackerySensorDescription, ...] = (
    PROP_SENSORS[0], PROP_SENSORS[1], PROP_SENSORS[2], PROP_SENSORS[3],
    PROP_SENSORS[8], PROP_SENSORS[9],
)

KNOWN_KEYS = {d.key for d in PROP_SENSORS} | BINARY_PROP_KEYS | {"bs"}


def _scaled(value: Any, d: JackerySensorDescription) -> Any:
    n = num(value)
    if n is None:
        return value if isinstance(value, str) else None
    if d.zero_is_none and n == 0:
        return None
    return round(n / d.scale, 2) if d.scale else n


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    created: set[str] = set()

    def add(entities: list[SensorEntity]) -> None:
        new = [e for e in entities if e.unique_id not in created]
        created.update(e.unique_id for e in new)
        if new:
            async_add_entities(new)

    # Static entities
    static: list[SensorEntity] = []
    for d in SYSTEM_SENSORS:
        static.append(SystemSensor(coordinator, d))
    for d, src in SYSTEM_ENERGY:
        static.append(SystemEnergySensor(coordinator, d, src))
    for dev in coordinator.devices.values():
        static.append(LastUpdatedSensor(coordinator, dev))
        static.append(RawPropertiesSensor(coordinator, dev))
        if dev.is_power_station:
            static += [DerivedSensor(coordinator, dev, d) for d in DERIVED_SENSORS]
            static += [DeviceEnergySensor(coordinator, dev, d, src) for d, src in DEVICE_ENERGY]
    add(static)

    @callback
    def discover() -> None:
        """Add entities for properties / packs that show up in the data."""
        data = coordinator.data or {}
        found: list[SensorEntity] = []
        for dev in coordinator.devices.values():
            dd = data.get(dev.dev_id) or {}
            props = dd.get("props") or {}
            for d in PROP_SENSORS:
                if d.key in props:
                    found.append(PropSensor(coordinator, dev, d))
            for key, value in props.items():
                if key not in KNOWN_KEYS and not isinstance(value, (dict, list)):
                    found.append(RawSensor(coordinator, dev, key))
            for idx, pack in enumerate(dd.get("packs") or [], start=1):
                for d in PACK_SENSORS:
                    if d.key in pack:
                        found.append(PackSensor(coordinator, dev, idx, pack, d))
        add(found)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


# ---------------------------------------------------------------- device sensors
class PropSensor(JackeryDeviceEntity, SensorEntity):
    entity_description: JackerySensorDescription

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice,
                 description: JackerySensorDescription) -> None:
        super().__init__(coordinator, dev, description.key, "sensor")
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return _scaled(self.dev_data.get("props", {}).get(self._key), self.entity_description)


class DerivedSensor(JackeryDeviceEntity, SensorEntity):
    entity_description: JackerySensorDescription

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice,
                 description: JackerySensorDescription) -> None:
        super().__init__(coordinator, dev, description.key, "sensor")
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return (self.dev_data.get("derived") or {}).get(self._key)


class LastUpdatedSensor(JackeryDeviceEntity, SensorEntity):
    _attr_name = "Last updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = _DIAG

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice) -> None:
        super().__init__(coordinator, dev, "last_updated", "sensor")

    @property
    def native_value(self) -> datetime | None:
        return self.dev_data.get("updated")


class RawSensor(JackeryDeviceEntity, SensorEntity):
    """Any property we don't have a friendly name for yet (e.g. Smart Transfer Switch fields)."""

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice, key: str) -> None:
        super().__init__(coordinator, dev, f"raw_{key.lower()}", "sensor")
        self._prop = key
        self._attr_name = f"Raw {key}"
        self._attr_icon = "mdi:code-braces"
        # Unknown devices (like the transfer switch): show everything.
        # Known power stations: create but leave disabled to avoid clutter.
        if dev.is_power_station and dev.profile.kind != "unknown":
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = _DIAG
        value = (coordinator.data or {}).get(dev.dev_id, {}).get("props", {}).get(key)
        if num(value) is not None:
            self._attr_state_class = M

    @property
    def native_value(self) -> Any:
        value = self.dev_data.get("props", {}).get(self._prop)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        if value is None:
            return None
        return str(value)[:255]


class RawPropertiesSensor(JackeryDeviceEntity, SensorEntity):
    """Diagnostic: every property the cloud reports, as attributes."""

    _attr_name = "Raw properties"
    _attr_icon = "mdi:code-json"
    _attr_entity_category = _DIAG
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice) -> None:
        super().__init__(coordinator, dev, "raw_properties", "sensor")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> int:
        return len(self.dev_data.get("props") or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "model": self.dev.profile.model,
            "kind": self.dev.profile.kind,
            "online": self.dev_data.get("online"),
            "error": self.dev_data.get("error"),
            "device_list_entry": {k: v for k, v in self.dev.raw.items()
                                  if k not in ("devSn", "deviceSn", "mac")},
        }
        for k, v in (self.dev_data.get("props") or {}).items():
            if k not in ("mac", "wip", "wname"):
                attrs[f"p_{k}"] = v if not isinstance(v, (dict, list)) else json.dumps(v)[:500]
        return attrs


class PackSensor(JackeryDeviceEntity, SensorEntity):
    entity_description: JackerySensorDescription

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice, idx: int,
                 pack: dict[str, Any], description: JackerySensorDescription) -> None:
        super().__init__(coordinator, dev,
                         f"pack{idx}_{FRIENDLY_KEYS.get(description.key, description.key)}", "sensor")
        self.entity_description = description
        self._idx = idx
        pack_sn = pack.get("deviceSn") or pack.get("devSn") or pack.get("sn")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(pack_sn) if pack_sn else f"{dev.dev_id}_pack{idx}")},
            name=f"{dev.name} Battery Pack {idx}",
            manufacturer=MANUFACTURER,
            model="Battery Pack 5000 Plus" if dev.profile.slug.startswith(("e5000", "hp5000")) else "Battery pack",
            via_device=(DOMAIN, dev.dev_id),
        )

    @property
    def native_value(self) -> Any:
        packs = self.dev_data.get("packs") or []
        if len(packs) < self._idx:
            return None
        return _scaled(packs[self._idx - 1].get(self.entity_description.key), self.entity_description)


class _EnergyMixin(RestoreSensor):
    """Integrates a power reading into kWh across polls; survives restarts.

    Must be listed *before* the coordinator entity base so its update hook runs first.
    """

    _acc: EnergyAccumulator

    async def async_added_to_hass(self) -> None:
        self._acc = EnergyAccumulator()
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            restored = num(last.native_value)
            if restored is not None:
                self._acc.total_kwh = float(restored)
        self._attr_native_value = round(self._acc.total_kwh, 4)

    def _power_now(self) -> float | None:
        raise NotImplementedError

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = round(self._acc.add(num(self._power_now()), time.time()), 4)
        super()._handle_coordinator_update()


class DeviceEnergySensor(_EnergyMixin, JackeryDeviceEntity):
    entity_description: JackerySensorDescription

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice,
                 description: JackerySensorDescription, source: str) -> None:
        super().__init__(coordinator, dev, description.key, "sensor")
        self.entity_description = description
        self._source = source

    def _power_now(self) -> float | None:
        if self.dev_data.get("error"):
            return None
        return (self.dev_data.get("derived") or {}).get(self._source)


# ---------------------------------------------------------------- system sensors
class SystemSensor(JackerySystemEntity, SensorEntity):
    entity_description: JackerySensorDescription

    def __init__(self, coordinator: JackeryCoordinator, description: JackerySensorDescription) -> None:
        super().__init__(coordinator, description.key, "sensor")
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.system.get(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key == "devices_online":
            return {"devices_total": self.system.get("devices_total")}
        return None


class SystemEnergySensor(_EnergyMixin, JackerySystemEntity):
    entity_description: JackerySensorDescription

    def __init__(self, coordinator: JackeryCoordinator, description: JackerySensorDescription,
                 source: str) -> None:
        super().__init__(coordinator, description.key, "sensor")
        self.entity_description = description
        self._source = source

    def _power_now(self) -> float | None:
        return self.system.get(self._source)
