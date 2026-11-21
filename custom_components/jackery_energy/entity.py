"""Base entity classes."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SYSTEM_SLUG
from .coordinator import JackeryCoordinator, JackeryDevice
from .logic import FRIENDLY_KEYS


def device_info(dev: JackeryDevice) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, dev.dev_id)},
        name=dev.name,
        manufacturer=MANUFACTURER,
        model=dev.profile.model,
        serial_number=dev.sn,
    )


def system_device_info(coordinator: JackeryCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"system_{coordinator.config_entry.entry_id}")},
        name="Jackery System",
        manufacturer=MANUFACTURER,
        model="Combined view of all Jackery devices",
    )


class JackeryDeviceEntity(CoordinatorEntity[JackeryCoordinator]):
    """Entity bound to one Jackery device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: JackeryCoordinator, dev: JackeryDevice, key: str,
                 platform: str) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._key = key
        ident = FRIENDLY_KEYS.get(key, key)
        self._attr_unique_id = f"{dev.dev_id}_{ident}"
        # Predictable entity ids so the shipped dashboard works: sensor.jackery_e5000_battery
        self.entity_id = f"{platform}.jackery_{dev.profile.slug}_{ident}"
        self._attr_device_info = device_info(dev)

    @property
    def dev_data(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self.dev.dev_id) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.dev_data) and not self.dev_data.get("error")


class JackerySystemEntity(CoordinatorEntity[JackeryCoordinator]):
    """Entity on the virtual 'Jackery System' device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: JackeryCoordinator, key: str, platform: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"system_{coordinator.config_entry.entry_id}_{key}"
        self.entity_id = f"{platform}.jackery_{SYSTEM_SLUG}_{key}"
        self._attr_device_info = system_device_info(coordinator)

    @property
    def system(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("_system") or {}
