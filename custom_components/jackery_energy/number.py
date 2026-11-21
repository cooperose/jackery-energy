"""Numbers: backup reserve and the nice-to-have threshold."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .control import BoxEntity, box_devices
from .mqtt import set_backup_reserve, set_nice_to_have_level

NUMBERS = (
    ("backup_reserve", "dt", "Backup reserve", "mdi:battery-lock", set_backup_reserve),
    ("nice_to_have_level", "ddt", "Nice-to-have circuits cut-off", "mdi:battery-alert-variant-outline",
     set_nice_to_have_level),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        BoxNumber(coordinator, dev, *n) for dev in box_devices(coordinator) for n in NUMBERS
    )


class BoxNumber(BoxEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, dev, key, prop, name, icon, builder) -> None:
        super().__init__(coordinator, dev, key, "number")
        self._prop, self._builder = prop, builder
        self._attr_name = name
        self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        return self.prop(self._prop)

    async def async_set_native_value(self, value: float) -> None:
        await self.send(self._builder(int(round(value))))
