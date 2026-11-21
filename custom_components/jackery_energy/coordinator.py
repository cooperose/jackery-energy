"""Data coordinator: one poll fetches every device on the account."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import JackeryAPI, JackeryAuthenticationError, JackeryError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, PACK_POLL_EVERY
from .logic import KIND_TRANSFER_SWITCH, DeviceProfile, derive_device, derive_system

_LOGGER = logging.getLogger(__name__)


@dataclass
class JackeryDevice:
    """Static info about a device discovered at setup."""

    dev_id: str
    sn: str | None
    name: str
    profile: DeviceProfile
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_power_station(self) -> bool:
        return self.profile.kind != KIND_TRANSFER_SWITCH


def _flatten_pack(pack: dict[str, Any]) -> dict[str, Any]:
    props = pack.get("properties")
    if isinstance(props, dict):
        return {**{k: v for k, v in pack.items() if k != "properties"}, **props}
    return pack


class JackeryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the Jackery cloud for all devices."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: JackeryAPI,
                 devices: list[JackeryDevice]) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.devices = {d.dev_id: d for d in devices}
        self._cycle = 0
        self._packs: dict[str, list[dict[str, Any]]] = {}

    def _fetch(self) -> dict[str, Any]:
        poll_packs = self._cycle % PACK_POLL_EVERY == 0
        self._cycle += 1
        out: dict[str, Any] = {}
        errors = 0

        for dev in self.devices.values():
            entry: dict[str, Any] = {"props": {}, "meta": {}, "online": None, "error": None}
            try:
                data = self.api.get_property(dev.dev_id)
                entry["props"] = data.get("properties") or {}
                entry["meta"] = data.get("device") or {}
                status = entry["meta"].get("onlineStatus")
                entry["online"] = None if status is None else status == 1
            except JackeryAuthenticationError:
                raise
            except JackeryError as err:
                errors += 1
                entry["error"] = str(err)
                _LOGGER.debug("Fetching %s failed: %s", dev.name, err)

            if dev.is_power_station and dev.sn and poll_packs:
                try:
                    self._packs[dev.dev_id] = [
                        _flatten_pack(p) for p in self.api.get_battery_packs(dev.sn)
                    ]
                except JackeryError as err:
                    _LOGGER.debug("Battery pack list for %s unavailable: %s", dev.name, err)
                    self._packs.setdefault(dev.dev_id, [])
            entry["packs"] = self._packs.get(dev.dev_id, [])
            out[dev.dev_id] = entry

        if errors and errors == len(self.devices):
            raise JackeryError("All devices failed to update")
        return out

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.hass.async_add_executor_job(self._fetch)
        except JackeryAuthenticationError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except JackeryError as err:
            raise UpdateFailed(str(err)) from err

        now = dt_util.utcnow()
        summary = []
        for dev_id, entry in data.items():
            dev = self.devices[dev_id]
            entry["updated"] = now
            entry["derived"] = (
                derive_device(entry["props"], dev.profile.capacity_wh, entry["packs"], entry["online"])
                if dev.is_power_station and entry["props"] else {}
            )
            summary.append({
                "kind": dev.profile.kind, "slug": dev.profile.slug, "online": entry["online"],
                "props": entry["props"], "derived": entry["derived"],
            })
        data["_system"] = derive_system(summary)
        return data
