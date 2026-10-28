"""Accu Planner: plant laden/ontladen van een thuisaccu op kwartierprijzen."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    ATTR_COST_CT,
    CONF_DISCHARGE_MARGIN,
    CONF_WEAR_COST,
    DEFAULTS,
    DOMAIN,
    OBSOLETE_KEYS,
    SERVICE_SET_COST_PRICE,
)
from .coordinator import AccuPlannerConfigEntry, AccuPlannerCoordinator
from .executor import PlanExecutor

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = f"/{DOMAIN}/accu-planner-card.js"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Maak de dashboardkaart (custom:accu-planner-card) beschikbaar."""
    path = Path(__file__).parent / "frontend" / "accu-planner-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    version = (await async_get_integration(hass, DOMAIN)).version
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Registreer de service en de dashboardkaart."""
    await _async_register_card(hass)

    async def _set_cost_price(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.async_set_cost_price(call.data[ATTR_COST_CT])

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_COST_PRICE,
        _set_cost_price,
        schema=vol.Schema({vol.Required(ATTR_COST_CT): vol.Coerce(float)}),
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: AccuPlannerConfigEntry) -> bool:
    """Migrate options: 1.2 merged wear cost and discharge margin, 1.3 removed 'allow export'."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        opts = dict(entry.options)
        if CONF_DISCHARGE_MARGIN in opts or CONF_WEAR_COST in opts:
            opts[CONF_WEAR_COST] = float(opts.get(CONF_WEAR_COST, 3)) + float(opts.get(CONF_DISCHARGE_MARGIN, 2))
        else:
            opts[CONF_WEAR_COST] = DEFAULTS[CONF_WEAR_COST]
        opts = {k: v for k, v in opts.items() if k not in OBSOLETE_KEYS}
        hass.config_entries.async_update_entry(entry, options=opts, minor_version=2)
    if entry.minor_version < 3:
        # 1.11: 'allow export' is derived from the discharge sell setting
        opts = {k: v for k, v in entry.options.items() if k not in OBSOLETE_KEYS}
        hass.config_entries.async_update_entry(entry, options=opts, minor_version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AccuPlannerConfigEntry) -> bool:
    """Zet een Accu Planner-instantie op."""
    coordinator = AccuPlannerCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    coordinator.executor = PlanExecutor(hass, coordinator)
    entry.runtime_data = coordinator
    coordinator.async_start_listeners()
    coordinator.executor.async_start()
    entry.async_on_unload(coordinator.executor.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: AccuPlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AccuPlannerConfigEntry) -> bool:
    """Verwijder een instantie."""
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
