"""Service: jackery_energy.rename_circuit."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import JackeryError
from .const import DOMAIN
from .mqtt import rename_circuit

SCHEMA = vol.Schema({
    vol.Required("circuit"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
    vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=24)),
})


def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "rename_circuit"):
        return

    async def _rename(call: ServiceCall) -> None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        for entry in entries:
            coordinator = entry.runtime_data
            for dev in coordinator.devices.values():
                if dev.profile.kind == "transfer_switch":
                    try:
                        await coordinator.async_box_command(
                            dev, [rename_circuit(call.data["circuit"], call.data["name"])])
                    except JackeryError as err:
                        raise HomeAssistantError(str(err)) from err
                    return
        raise HomeAssistantError("No Smart Transfer Switch found")

    hass.services.async_register(DOMAIN, "rename_circuit", _rename, schema=SCHEMA)
