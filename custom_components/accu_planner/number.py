"""Number entities: adjustable settings on the device page (slider)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DIS_SPEED
from .coordinator import AccuPlannerConfigEntry
from .switch import _device


async def async_setup_entry(
    hass: HomeAssistant, entry: AccuPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DischargeSpeedNumber(entry)])


class DischargeSpeedNumber(NumberEntity):
    """Reaction speed of the discharge regulator: 1 = slow (ignore short peaks) .. 10 = fast."""

    _attr_has_entity_name = True
    _attr_translation_key = CONF_DIS_SPEED
    _attr_icon = "mdi:speedometer"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1

    def __init__(self, entry: AccuPlannerConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{CONF_DIS_SPEED}"
        self._attr_device_info = _device(entry)

    @property
    def native_value(self) -> float:
        return float(self._entry.runtime_data.conf.get(CONF_DIS_SPEED) or 4)

    async def async_set_native_value(self, value: float) -> None:
        await self._entry.runtime_data.async_set_option(CONF_DIS_SPEED, int(round(value)))
        self.async_write_ha_state()
