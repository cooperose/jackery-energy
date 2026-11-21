"""Selects: working mode and per-circuit backup priority."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CIRCUIT_PRIORITIES, WORK_MODES
from .control import BoxEntity, box_devices, circuit_label
from .mqtt import set_priorities, set_work_mode

_MODE_BY_NAME = {v: k for k, v in WORK_MODES.items()}
_PRIO_BY_NAME = {v: k for k, v in CIRCUIT_PRIORITIES.items()}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    created: set[str] = set()

    def add(entities):
        new = [e for e in entities if e.unique_id not in created]
        created.update(e.unique_id for e in new)
        if new:
            async_add_entities(new)

    add([WorkModeSelect(coordinator, dev) for dev in box_devices(coordinator)])

    @callback
    def discover() -> None:
        found = []
        for dev in box_devices(coordinator):
            for c in (coordinator.data or {}).get(dev.dev_id, {}).get("circuits") or []:
                found.append(CircuitPrioritySelect(coordinator, dev, int(c["idx"])))
        add(found)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class WorkModeSelect(BoxEntity, SelectEntity):
    _attr_name = "Working mode"
    _attr_translation_key = "work_mode"
    _attr_icon = "mdi:cog-transfer"
    _attr_options = list(WORK_MODES.values())

    def __init__(self, coordinator, dev) -> None:
        super().__init__(coordinator, dev, "work_mode", "select")

    @property
    def current_option(self) -> str | None:
        v = self.prop("en")
        return None if v is None else WORK_MODES.get(int(v))

    async def async_select_option(self, option: str) -> None:
        await self.send(set_work_mode(_MODE_BY_NAME[option]))


class CircuitPrioritySelect(BoxEntity, SelectEntity):
    _attr_translation_key = "circuit_priority"
    _attr_icon = "mdi:sort-numeric-ascending"
    _attr_options = list(CIRCUIT_PRIORITIES.values())

    def __init__(self, coordinator, dev, idx: int) -> None:
        super().__init__(coordinator, dev, f"circuit_{idx}_priority", "select")
        self._idx = idx

    @property
    def name(self) -> str:
        c = self.circuit(self._idx)
        return (circuit_label(c) if c else f"Circuit {self._idx}") + " priority"

    @property
    def available(self) -> bool:
        return super().available and self.circuit(self._idx) is not None

    @property
    def current_option(self) -> str | None:
        c = self.circuit(self._idx)
        return None if c is None or c.get("pr") is None else CIRCUIT_PRIORITIES.get(int(c["pr"]))

    async def async_select_option(self, option: str) -> None:
        prios = {int(c["idx"]): int(c.get("pr") or 0) for c in self.circuits()}
        prios[self._idx] = _PRIO_BY_NAME[option]
        await self.send(set_priorities(prios))
