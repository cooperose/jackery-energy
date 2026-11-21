"""Product picture for each Jackery device (the same image the Jackery app shows)."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import JackeryCoordinator, JackeryDevice
from .entity import JackeryDeviceEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: JackeryCoordinator = entry.runtime_data
    async_add_entities(
        JackeryPicture(hass, coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.raw.get("iconPath")
    )


class JackeryPicture(JackeryDeviceEntity, ImageEntity):
    _attr_name = "Picture"
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: JackeryCoordinator, dev: JackeryDevice) -> None:
        JackeryDeviceEntity.__init__(self, coordinator, dev, "picture", "image")
        ImageEntity.__init__(self, hass)
        self._attr_image_url = dev.raw["iconPath"]
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self) -> bool:
        return True
