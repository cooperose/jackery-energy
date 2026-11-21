"""Jackery Energy: extended Jackery cloud integration (E5000 Plus, E300 Plus, Smart Transfer Switch)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from datetime import timedelta

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .api import JackeryAPI, JackeryAuthenticationError, JackeryError
from .coordinator import JackeryCoordinator, JackeryDevice
from .logic import classify, unique_slugs

_LOGGER = logging.getLogger(__name__)

from .const import CONF_CIRCUIT_INTERVAL, DEFAULT_CIRCUIT_INTERVAL
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.SENSOR, Platform.BINARY_SENSOR, Platform.IMAGE,
    Platform.SWITCH, Platform.SELECT, Platform.NUMBER, Platform.BUTTON,
]

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
    async_setup_services(hass)

    if any(not d.is_power_station for d in devices):
        minutes = entry.options.get(CONF_CIRCUIT_INTERVAL, DEFAULT_CIRCUIT_INTERVAL)
        # First circuit read shortly after start-up, then on the chosen interval.
        entry.async_on_unload(async_call_later(hass, 20, coordinator.async_refresh_box))
        if minutes:
            entry.async_on_unload(async_track_time_interval(
                hass, coordinator.async_refresh_box, timedelta(minutes=minutes)))
    return True


async def _async_reload(hass: HomeAssistant, entry: JackeryConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
