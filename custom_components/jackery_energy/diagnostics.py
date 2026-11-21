"""Diagnostics download: a full dump of what the Jackery cloud reports for each device.

Settings → Devices & services → Jackery Energy → ⋮ → Download diagnostics.
Share this file to get friendly names added for Smart Transfer Switch fields.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .api import JackeryError

TO_REDACT = {
    CONF_USERNAME, CONF_PASSWORD, "devSn", "deviceSn", "sn", "mac", "wip", "wname",
    "token", "userId", "bindUserId", "account", "userName", "email", "ac1_sn", "ac2_sn",
    "bluetoothKey",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    devices: list[dict[str, Any]] = []

    for dev in coordinator.devices.values():
        today: Any = None
        if dev.sn:
            try:
                today = await hass.async_add_executor_job(coordinator.api.get_today_stats, dev.sn)
            except JackeryError as err:
                today = f"unavailable: {err}"
        dd = data.get(dev.dev_id) or {}
        devices.append({
            "name": dev.name,
            "slug": dev.profile.slug,
            "model": dev.profile.model,
            "kind": dev.profile.kind,
            "device_list_entry": dev.raw,
            "online": dd.get("online"),
            "error": dd.get("error"),
            "meta": dd.get("meta"),
            "properties": dd.get("props"),
            "derived": dd.get("derived"),
            "battery_packs": dd.get("packs"),
            "today_stats": today,
        })

    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "devices": devices,
            "system": data.get("_system"),
        },
        TO_REDACT,
    )
