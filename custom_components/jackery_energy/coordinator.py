"""Data coordinator: one poll fetches every device on the account."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import JackeryAPI, JackeryAuthenticationError, JackeryError
from .mqtt import BoxCommand, JackeryMqtt, decode_name, q_circuits, q_device
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


def _expand_nested(props: dict[str, Any]) -> dict[str, Any]:
    """Some devices (Smart Transfer Switch) send JSON strings, e.g. ac1='{"rb": 79, ...}'.

    Decode them and add flattened scalar keys (ac1_rb, fz_ol, ...) next to the original.
    """
    out = dict(props)
    for key, value in props.items():
        if isinstance(value, str) and value.startswith("{"):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            out[key] = value
            for sub, sv in value.items():
                if not isinstance(sv, (dict, list)):
                    out[f"{key}_{sub}"] = sv
    return out


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
        # Smart Transfer Switch data that only arrives over MQTT (circuits, ...)
        self.box_state: dict[str, dict[str, Any]] = {}
        self._mqtt: JackeryMqtt | None = None

    # ------------------------------------------------------------------ MQTT
    @property
    def mqtt(self) -> JackeryMqtt | None:
        if self._mqtt is None and self.api.user_id and self.api.mqtt_password:
            self._mqtt = JackeryMqtt(self.api.user_id, self.api.mac_id, self.api.mqtt_password)
        return self._mqtt

    def _ingest(self, messages: list[dict[str, Any]]) -> None:
        by_sn = {d.sn: d for d in self.devices.values() if d.sn}
        for msg in messages:
            dev = by_sn.get(str(msg.get("deviceSn")))
            if dev is None:
                continue
            body = msg.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except ValueError:
                    continue
            if not isinstance(body, dict):
                continue
            if isinstance(body.get("properties"), dict):
                body = body["properties"]
            state = self.box_state.setdefault(dev.dev_id, {"props": {}, "circuits": []})
            cir = body.get("cir")
            if isinstance(cir, str):
                try:
                    cir = json.loads(cir)
                except ValueError:
                    cir = None
            if isinstance(cir, list):
                circuits = []
                for c in cir:
                    if isinstance(c, dict) and c.get("idx") is not None:
                        circuits.append({**c, "name": decode_name(c.get("nm"))})
                if circuits:
                    state["circuits"] = sorted(circuits, key=lambda c: int(c["idx"]))
            for k, v in body.items():
                if k not in ("cir", "cmd") and not isinstance(v, (dict, list)):
                    state["props"][k] = v
            state["updated"] = dt_util.utcnow()

    async def async_box_command(self, dev: JackeryDevice, commands: list[BoxCommand],
                                listen_s: float = 6.0) -> None:
        """Send commands to a transfer switch, then re-read its state and circuits."""
        client = self.mqtt
        if client is None or not dev.sn:
            raise JackeryError("Control unavailable: login did not return MQTT credentials")
        try:
            msgs = await self.hass.async_add_executor_job(
                client.exchange, dev.sn, [*commands, q_device(), q_circuits()], listen_s
            )
        except Exception as err:  # noqa: BLE001
            self.box_state.setdefault(dev.dev_id, {"props": {}, "circuits": []})["error"] = str(err)
            raise JackeryError(f"Transfer switch command failed: {err}") from err
        self._ingest(msgs)
        self.box_state.setdefault(dev.dev_id, {"props": {}, "circuits": []})["error"] = None
        await self.async_request_refresh()

    async def async_refresh_box(self, *_: Any) -> None:
        """Poll circuits + settings from every transfer switch (briefly bumps the phone app)."""
        for dev in self.devices.values():
            if dev.is_power_station or not dev.sn or self.mqtt is None:
                continue
            try:
                msgs = await self.hass.async_add_executor_job(
                    self.mqtt.exchange, dev.sn, [q_device(), q_circuits()], 6.0
                )
                self._ingest(msgs)
                self.box_state.setdefault(dev.dev_id, {"props": {}, "circuits": []})["error"] = None
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Transfer switch MQTT refresh failed: %s", err)
                self.box_state.setdefault(dev.dev_id, {"props": {}, "circuits": []})["error"] = str(err)
        if self.data:
            self._merge_box(self.data)
            self.async_set_updated_data(self.data)

    def _merge_box(self, data: dict[str, Any]) -> None:
        for dev_id, state in self.box_state.items():
            entry = data.get(dev_id)
            if entry is None:
                continue
            # HTTP values win; MQTT fills in what HTTP doesn't report.
            entry["props"] = {**state.get("props", {}), **entry.get("props", {})}
            entry["circuits"] = state.get("circuits", [])
            entry["box_updated"] = state.get("updated")
            entry["box_error"] = state.get("error")

    def _fetch(self) -> dict[str, Any]:
        poll_packs = self._cycle % PACK_POLL_EVERY == 0
        self._cycle += 1
        out: dict[str, Any] = {}
        errors = 0

        for dev in self.devices.values():
            entry: dict[str, Any] = {"props": {}, "meta": {}, "online": None, "error": None}
            try:
                data = self.api.get_property(dev.dev_id)
                entry["props"] = _expand_nested(data.get("properties") or {})
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
        self._merge_box(data)
        return data
