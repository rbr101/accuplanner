"""Schakelaar 'Accu-plan uitvoeren'."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import AccuPlannerConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: AccuPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ExecutePlanSwitch(entry)])


class ExecutePlanSwitch(RestoreEntity, SwitchEntity):
    """Aan: de accu volgt het laadschema. Stand blijft bewaard na een herstart."""

    _attr_has_entity_name = True
    _attr_translation_key = "execute_plan"
    _attr_icon = "mdi:battery-sync"

    def __init__(self, entry: AccuPlannerConfigEntry) -> None:
        self._entry = entry
        self._executor = entry.runtime_data.executor
        self._attr_unique_id = f"{entry.entry_id}_execute_plan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Accu Planner",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._executor.add_state_listener(self._changed)
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self._executor.enabled = True
            self.hass.async_create_task(self._executor.async_apply("restart"))

    @callback
    def _changed(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._executor.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ex = self._executor
        return {
            "target_mode": ex.target,
            "last_command": ex.last_command,
            "last_command_at": ex.last_command_at.isoformat() if ex.last_command_at else None,
            "last_error": ex.last_error,
            "solar_power": ex.solar.output if ex.target == "solar" else None,
            "solar_status": ex.solar_status,
            "discharge_power": ex.discharge.output if ex.target == "dis" else None,
            "discharge_status": ex.discharge_status,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._executor.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._executor.async_set_enabled(False)
