"""Binary sensors for Jackery Energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import JackeryDeviceEntity, JackerySystemEntity
from .logic import ACTIVE_W, num

PWR = BinarySensorDeviceClass.POWER

# On/off flags straight from the device properties.
PROP_BINARIES: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(key="oac", name="AC output", device_class=PWR, icon="mdi:power-socket-us"),
    BinarySensorEntityDescription(key="odc", name="DC output", device_class=PWR, icon="mdi:current-dc"),
    BinarySensorEntityDescription(key="odcu", name="USB output", device_class=PWR, icon="mdi:usb-port"),
    BinarySensorEntityDescription(key="odcc", name="Car output", device_class=PWR, icon="mdi:car-electric"),
    BinarySensorEntityDescription(key="iac", name="AC input enabled", device_class=PWR, icon="mdi:power-plug"),
    BinarySensorEntityDescription(key="idc", name="DC input enabled", device_class=PWR, icon="mdi:solar-panel"),
    BinarySensorEntityDescription(key="ups", name="UPS mode", icon="mdi:shield-sync",
                                  entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="sfc", name="Super charge", icon="mdi:flash",
                                  entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="wss", name="Wireless charging", device_class=PWR,
                                  entity_category=EntityCategory.DIAGNOSTIC),
    # Smart Transfer Switch
    BinarySensorEntityDescription(key="ac1_bi", name="Port 1 power station connected",
                                  device_class=BinarySensorDeviceClass.PLUG),
    BinarySensorEntityDescription(key="ac2_bi", name="Port 2 power station connected",
                                  device_class=BinarySensorDeviceClass.PLUG),
)

# Fault flags: anything non-zero is a problem.
PROBLEM_BINARIES: tuple[BinarySensorEntityDescription, ...] = tuple(
    BinarySensorEntityDescription(key=k, name=n, device_class=BinarySensorDeviceClass.PROBLEM)
    for k, n in (
        ("fz_ol", "Overload"), ("fz_ta1", "Temperature alarm 1"), ("fz_ta2", "Temperature alarm 2"),
        ("fz_ntc", "Temperature sensor fault"), ("fz_rtc", "Clock fault"),
    )
)
BINARY_PROP_KEYS = {d.key for d in PROP_BINARIES}


BINARY_PROP_KEYS |= {d.key for d in PROBLEM_BINARIES}


@dataclass(frozen=True, kw_only=True)
class DerivedBinaryDescription(BinarySensorEntityDescription):
    fn: Callable[[dict[str, Any]], bool | None]


def _active(key: str) -> Callable[[dict[str, Any]], bool | None]:
    def fn(d: dict[str, Any]) -> bool | None:
        v = num(d.get(key))
        return None if v is None else v > ACTIVE_W
    return fn


DERIVED_BINARIES: tuple[DerivedBinaryDescription, ...] = (
    DerivedBinaryDescription(key="charging", name="Charging",
                             device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
                             fn=lambda d: None if d.get("battery_state") is None
                             else d["battery_state"] == "charging"),
    DerivedBinaryDescription(key="solar_active", name="Solar producing", icon="mdi:solar-power",
                             device_class=PWR, fn=_active("solar_power")),
    DerivedBinaryDescription(key="grid_input_active", name="Charging from grid",
                             icon="mdi:battery-charging-high", device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
                             fn=_active("grid_input_power")),
)

SYSTEM_BINARIES: tuple[DerivedBinaryDescription, ...] = (
    DerivedBinaryDescription(key="solar_active", name="Solar producing", icon="mdi:solar-power",
                             device_class=PWR, fn=_active("solar_power")),
    DerivedBinaryDescription(key="grid_input_active", name="Charging from grid",
                             icon="mdi:battery-charging-high", device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
                             fn=_active("grid_charge_power")),
    DerivedBinaryDescription(key="grid_to_circuits", name="Circuits on grid",
                             icon="mdi:transmission-tower", device_class=PWR, fn=_active("grid_to_circuits_power")),
    DerivedBinaryDescription(key="on_battery", name="Running on battery", icon="mdi:home-battery",
                             fn=lambda d: None if d.get("power_source") is None
                             else d["power_source"] == "battery"),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    created: set[str] = set()

    def add(entities: list[BinarySensorEntity]) -> None:
        new = [e for e in entities if e.unique_id not in created]
        created.update(e.unique_id for e in new)
        if new:
            async_add_entities(new)

    static: list[BinarySensorEntity] = [SystemBinary(coordinator, d) for d in SYSTEM_BINARIES]
    for dev in coordinator.devices.values():
        static.append(OnlineBinary(coordinator, dev))
        if dev.is_power_station:
            static += [DerivedBinary(coordinator, dev, d) for d in DERIVED_BINARIES]
    add(static)

    @callback
    def discover() -> None:
        data = coordinator.data or {}
        found: list[BinarySensorEntity] = []
        for dev in coordinator.devices.values():
            props = (data.get(dev.dev_id) or {}).get("props") or {}
            found += [PropBinary(coordinator, dev, d) for d in PROP_BINARIES if d.key in props]
            found += [ProblemBinary(coordinator, dev, d) for d in PROBLEM_BINARIES if d.key in props]
        add(found)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class PropBinary(JackeryDeviceEntity, BinarySensorEntity):
    def __init__(self, coordinator, dev, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, dev, description.key, "binary_sensor")
        self.entity_description = description
        if dev.profile.kind == "transfer_switch" and description.key == "ups":
            self._attr_name = "Backup ready"
            self._attr_entity_category = None

    @property
    def is_on(self) -> bool | None:
        v = num(self.dev_data.get("props", {}).get(self._key))
        return None if v is None else v == 1


class ProblemBinary(PropBinary):
    @property
    def is_on(self) -> bool | None:
        v = num(self.dev_data.get("props", {}).get(self._key))
        return None if v is None else v != 0


class DerivedBinary(JackeryDeviceEntity, BinarySensorEntity):
    entity_description: DerivedBinaryDescription

    def __init__(self, coordinator, dev, description: DerivedBinaryDescription) -> None:
        super().__init__(coordinator, dev, description.key, "binary_sensor")
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.fn(self.dev_data.get("derived") or {})


class OnlineBinary(JackeryDeviceEntity, BinarySensorEntity):
    """Is the device connected to the Jackery cloud?"""

    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, dev) -> None:
        super().__init__(coordinator, dev, "online", "binary_sensor")
        # Product picture, so tile cards can show the device instead of an icon.
        self._attr_entity_picture = dev.raw.get("iconPath")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and bool(self.dev_data)

    @property
    def is_on(self) -> bool | None:
        if self.dev_data.get("error"):
            return False
        return self.dev_data.get("online")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meta = self.dev_data.get("meta") or {}
        return {
            "model": self.dev.profile.model,
            "shared_with_me": self.dev.raw.get("shared"),
            "online_time": meta.get("onlineTime"),
            "offline_time": meta.get("offlineTime"),
            "last_error": self.dev_data.get("error"),
        }


class SystemBinary(JackerySystemEntity, BinarySensorEntity):
    entity_description: DerivedBinaryDescription

    def __init__(self, coordinator, description: DerivedBinaryDescription) -> None:
        super().__init__(coordinator, description.key, "binary_sensor")
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.fn(self.system)
