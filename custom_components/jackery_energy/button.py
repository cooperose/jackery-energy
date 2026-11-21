"""Buttons: charge the 5000 Plus from the grid, stop, refresh circuits."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GRID_CHARGE_MINUTES
from .control import BoxEntity, box_devices
from .mqtt import set_backup_only

BUTTONS = (
    ("charge_from_grid", "Charge 5000 Plus from grid", "mdi:battery-charging-high",
     lambda: [set_backup_only(True, GRID_CHARGE_MINUTES)]),
    ("stop_grid_charge", "Stop grid charging", "mdi:battery-off-outline",
     lambda: [set_backup_only(False)]),
    ("refresh_circuits", "Refresh circuits", "mdi:refresh", lambda: []),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BoxButton(coordinator, dev, *b) for dev in box_devices(coordinator) for b in BUTTONS)


class BoxButton(BoxEntity, ButtonEntity):
    def __init__(self, coordinator, dev, key, name, icon, build) -> None:
        super().__init__(coordinator, dev, key, "button")
        self._attr_name = name
        self._attr_icon = icon
        self._build = build

    async def async_press(self) -> None:
        await self.send(*self._build())
