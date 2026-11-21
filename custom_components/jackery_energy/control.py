"""Shared base for Smart Transfer Switch control entities."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .api import JackeryError
from .coordinator import JackeryCoordinator, JackeryDevice
from .entity import JackeryDeviceEntity
from .logic import num
from .mqtt import BoxCommand


def box_devices(coordinator: JackeryCoordinator) -> list[JackeryDevice]:
    return [d for d in coordinator.devices.values() if d.profile.kind == "transfer_switch"]


class BoxEntity(JackeryDeviceEntity):
    """Entity that reads switch state from props/circuits and sends MQTT commands."""

    def prop(self, key: str) -> float | None:
        return num(self.dev_data.get("props", {}).get(key))

    def circuits(self) -> list[dict[str, Any]]:
        return self.dev_data.get("circuits") or []

    def circuit(self, idx: int) -> dict[str, Any] | None:
        return next((c for c in self.circuits() if int(c.get("idx", -1)) == idx), None)

    async def send(self, *commands: BoxCommand) -> None:
        try:
            await self.coordinator.async_box_command(self.dev, list(commands))
        except JackeryError as err:
            raise HomeAssistantError(str(err)) from err


def circuit_label(c: dict[str, Any]) -> str:
    name = c.get("name")
    return f"Circuit {c['idx']}" + (f" · {name}" if name else "")
