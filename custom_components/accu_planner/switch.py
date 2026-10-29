"""Switches: 'Execute battery plan' plus the on/off settings of the integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_COST_FLOOR,
    CONF_CTRL_CHARGE_MAX,
    CONF_CTRL_SAFE_ON_OFF,
    CONF_DIS_CAR_BLOCK,
    CONF_DIS_ENABLED,
    CONF_DIS_SELL_ENABLED,
    CONF_PLAN_ENABLED,
    CONF_SOLAR_BELOW_DISCHARGE,
    CONF_SOLAR_ENABLED,
    DOMAIN,
)
from .coordinator import AccuPlannerConfigEntry

# option key -> icon (same order as in the Configure menu)
SETTING_SWITCHES: dict[str, str] = {
    CONF_PLAN_ENABLED: "mdi:battery-clock",
    CONF_CTRL_CHARGE_MAX: "mdi:battery-arrow-up",
    CONF_CTRL_SAFE_ON_OFF: "mdi:battery-sync-outline",
    CONF_SOLAR_ENABLED: "mdi:solar-power",
    CONF_SOLAR_BELOW_DISCHARGE: "mdi:solar-power-variant",
    CONF_DIS_ENABLED: "mdi:battery-arrow-down",
    CONF_DIS_SELL_ENABLED: "mdi:cash-plus",
    CONF_DIS_CAR_BLOCK: "mdi:car-electric",
    CONF_COST_FLOOR: "mdi:floor-plan",
}


def _device(entry: AccuPlannerConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Accu Planner",
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: AccuPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[SwitchEntity] = [ExecutePlanSwitch(entry)]
    entities += [SettingSwitch(entry, key, icon) for key, icon in SETTING_SWITCHES.items()]
    async_add_entities(entities)


class ExecutePlanSwitch(RestoreEntity, SwitchEntity):
    """On: the battery follows the charge plan. The state is kept across restarts."""

    _attr_has_entity_name = True
    _attr_translation_key = "execute_plan"
    _attr_icon = "mdi:battery-sync"

    def __init__(self, entry: AccuPlannerConfigEntry) -> None:
        self._entry = entry
        self._executor = entry.runtime_data.executor
        self._attr_unique_id = f"{entry.entry_id}_execute_plan"
        self._attr_device_info = _device(entry)

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


class SettingSwitch(SwitchEntity):
    """An on/off setting from the Configure menu; changes are saved in the options without a reload."""

    _attr_has_entity_name = True

    def __init__(self, entry: AccuPlannerConfigEntry, key: str, icon: str) -> None:
        self._entry = entry
        self._key = key
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device(entry)

    @property
    def is_on(self) -> bool:
        return bool(self._entry.runtime_data.conf.get(self._key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        await self._entry.runtime_data.async_set_option(self._key, value)
        self.async_write_ha_state()
