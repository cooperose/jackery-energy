"""Switches: transfer-switch settings and one switch per home circuit."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GRID_CHARGE_MINUTES
from .control import BoxEntity, box_devices, circuit_label
from .mqtt import set_backup_only, set_circuit, set_supply_from_station, set_ups

SETTINGS = (
    # key, prop, name, icon, builder
    ("ups_mode", "ups", "UPS mode", "mdi:shield-sync", set_ups),
    ("backup_only", "ps", "Backup-only mode (charge from grid)", "mdi:battery-charging-high",
     lambda on: set_backup_only(on, GRID_CHARGE_MINUTES)),
    ("supply_from_station", "pss", "Power home from 5000 Plus", "mdi:home-battery", set_supply_from_station),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    created: set[str] = set()

    def add(entities):
        new = [e for e in entities if e.unique_id not in created]
        created.update(e.unique_id for e in new)
        if new:
            async_add_entities(new)

    add([SettingSwitch(coordinator, dev, *s) for dev in box_devices(coordinator) for s in SETTINGS])

    @callback
    def discover() -> None:
        found = []
        for dev in box_devices(coordinator):
            for c in (coordinator.data or {}).get(dev.dev_id, {}).get("circuits") or []:
                found.append(CircuitSwitch(coordinator, dev, int(c["idx"])))
        add(found)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SettingSwitch(BoxEntity, SwitchEntity):
    def __init__(self, coordinator, dev, key, prop, name, icon, builder) -> None:
        super().__init__(coordinator, dev, key, "switch")
        self._prop, self._builder = prop, builder
        self._attr_name = name
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        v = self.prop(self._prop)
        return None if v is None else v == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.send(self._builder(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.send(self._builder(False))


class CircuitSwitch(BoxEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_icon = "mdi:electric-switch"

    def __init__(self, coordinator, dev, idx: int) -> None:
        super().__init__(coordinator, dev, f"circuit_{idx}", "switch")
        self._idx = idx

    @property
    def name(self) -> str:
        c = self.circuit(self._idx)
        return circuit_label(c) if c else f"Circuit {self._idx}"

    @property
    def available(self) -> bool:
        return super().available and self.circuit(self._idx) is not None

    @property
    def is_on(self) -> bool | None:
        c = self.circuit(self._idx)
        return None if c is None or c.get("sw") is None else int(c["sw"]) == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.circuit(self._idx) or {}
        return {"index": self._idx, "circuit_name": c.get("name"), "power_w": c.get("pc"),
                "priority": c.get("pr"), "split_phase_with": c.get("sph") or None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.send(set_circuit(self._idx, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.send(set_circuit(self._idx, False))
