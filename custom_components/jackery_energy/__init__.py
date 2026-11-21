"""Jackery Energy: extended Jackery cloud integration (E5000 Plus, E300 Plus, Smart Transfer Switch)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import JackeryAPI, JackeryAuthenticationError, JackeryError
from .coordinator import JackeryCoordinator, JackeryDevice
from .logic import classify, unique_slugs

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.IMAGE]

type JackeryConfigEntry = ConfigEntry[JackeryCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    api = JackeryAPI(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        raw_devices = await hass.async_add_executor_job(api.get_devices)
    except JackeryAuthenticationError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except JackeryError as err:
        raise ConfigEntryNotReady(str(err)) from err

    if not raw_devices:
        raise ConfigEntryNotReady("No Jackery devices found on this account")

    profiles = [classify(d) for d in raw_devices]
    unique_slugs(profiles)
    devices = [
        JackeryDevice(
            dev_id=str(d["devId"]),
            sn=d.get("devSn") or d.get("deviceSn"),
            name=d.get("devNickname") or d.get("devName") or p.model,
            profile=p,
            raw=d,
        )
        for d, p in zip(raw_devices, profiles)
    ]
    for dev in devices:
        _LOGGER.info("Jackery device %s -> %s (%s, slug %s)",
                     dev.name, dev.profile.model, dev.profile.kind, dev.profile.slug)

    coordinator = JackeryCoordinator(hass, entry, api, devices)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: JackeryConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
