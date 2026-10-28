"""Config flow en opties voor Accu Planner."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_PLAN_ENABLED,
    OBSOLETE_KEYS,
    CONF_DIS_ENABLED,
    CONF_DIS_HYSTERESIS,
    CONF_DIS_MAX_POWER,
    CONF_DIS_MIN_POWER,
    CONF_DIS_MIN_SOC,
    CONF_DIS_SELL_ENABLED,
    CONF_DIS_SELL_POWER,
    CONF_SOLAR_BELOW_DISCHARGE,
    CONF_SOLAR_ENABLED,
    CONF_SOLAR_EXPORT_ENTITY,
    CONF_SOLAR_IMPORT_ENTITY,
    CONF_SOLAR_MAX_POWER,
    CONF_SOLAR_MIN_POWER,
    CONF_SOLAR_PV_ENTITY,
    CONF_CTRL_CHARGE_MAX,
    CONF_CTRL_MODE_ENTITY,
    CONF_CTRL_OPT_CHARGE,
    CONF_CTRL_OPT_DISCHARGE,
    CONF_CTRL_POWER_DISCHARGE,
    CONF_CTRL_POWER_ENTITY,
    CONF_CTRL_SAFE_ON_OFF,
    CONF_PROFIT_PCT,
    CONF_CAPACITY_SENSOR,
    CONF_CHARGE_POWER,
    CONF_COST_FLOOR,
    CONF_DISCHARGE_POWER,
    CONF_EFFICIENCY,
    CONF_MAX_SOC,
    CONF_MIN_SOC,
    CONF_PRICE_SENSOR,
    CONF_PROFILE,
    CONF_PROFILE_WEEKEND,
    CONF_PV_POWER_SENSOR,
    CONF_SELL_PRICE_SENSOR,
    CONF_SOC_SENSOR,
    CONF_SOLAR_COST,
    CONF_UPDATE_INTERVAL,
    CONF_WEAR_COST,
    DEFAULTS,
    DOMAIN,
)
from .planner import parse_profile


def _num(lo: float, hi: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=lo, max=hi, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX)
    )


def _schema(cur: dict[str, Any], with_name: bool) -> vol.Schema:
    d = {**DEFAULTS, **cur}
    sensor = EntitySelector(EntitySelectorConfig(domain="sensor"))
    fields: dict[Any, Any] = {}
    if with_name:  # first setup: the battery sensors are needed to start
        fields[vol.Required(CONF_NAME, default=d.get(CONF_NAME, "Accu Planner"))] = TextSelector()
        fields[vol.Required(CONF_CAPACITY_SENSOR, default=d[CONF_CAPACITY_SENSOR])] = sensor
        fields[vol.Required(CONF_SOC_SENSOR, default=d[CONF_SOC_SENSOR])] = sensor
    fields.update(
        {
            vol.Required(CONF_PRICE_SENSOR, default=d[CONF_PRICE_SENSOR]): sensor,
            vol.Optional(
                CONF_SELL_PRICE_SENSOR, description={"suggested_value": d[CONF_SELL_PRICE_SENSOR] or None}
            ): sensor,
            vol.Optional(
                CONF_PV_POWER_SENSOR, description={"suggested_value": d[CONF_PV_POWER_SENSOR] or None}
            ): sensor,
            vol.Required(CONF_PROFILE, default=d[CONF_PROFILE]): TextSelector(),
            vol.Optional(
                CONF_PROFILE_WEEKEND, description={"suggested_value": d[CONF_PROFILE_WEEKEND]}
            ): TextSelector(TextSelectorConfig()),
            vol.Required(CONF_EFFICIENCY, default=d[CONF_EFFICIENCY]): _num(50, 100, 1, "%"),
            vol.Required(CONF_WEAR_COST, default=d[CONF_WEAR_COST]): _num(0, 50, 0.1, "ct/kWh"),
            vol.Required(CONF_SOLAR_COST, default=d[CONF_SOLAR_COST]): _num(0, 50, 0.1, "ct/kWh"),
            vol.Required(CONF_CHARGE_POWER, default=d[CONF_CHARGE_POWER]): _num(100, 50000, 100, "W"),
            vol.Required(CONF_DISCHARGE_POWER, default=d[CONF_DISCHARGE_POWER]): _num(100, 50000, 100, "W"),
            vol.Required(CONF_MIN_SOC, default=d[CONF_MIN_SOC]): _num(0, 100, 1, "%"),
            vol.Required(CONF_MAX_SOC, default=d[CONF_MAX_SOC]): _num(1, 100, 1, "%"),
            vol.Required(CONF_COST_FLOOR, default=d[CONF_COST_FLOOR]): BooleanSelector(),
            vol.Required(CONF_PROFIT_PCT, default=d[CONF_PROFIT_PCT]): _num(0, 500, 1, "%"),
            vol.Required(CONF_UPDATE_INTERVAL, default=d[CONF_UPDATE_INTERVAL]): _num(1, 60, 1, "min"),
        }
    )
    return vol.Schema(fields)


def _validate(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    profile, bad = parse_profile(data.get(CONF_PROFILE))
    if bad or (not profile.blocks and profile.rest == 0):
        errors[CONF_PROFILE] = "invalid_profile"
    if data.get(CONF_PROFILE_WEEKEND) and parse_profile(data[CONF_PROFILE_WEEKEND])[1]:
        errors[CONF_PROFILE_WEEKEND] = "invalid_profile"
    if data.get(CONF_MIN_SOC, 0) >= data.get(CONF_MAX_SOC, 100):
        errors[CONF_MAX_SOC] = "min_above_max"
    return errors


class AccuPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Eerste configuratie."""

    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                data = dict(user_input)
                title = data.pop(CONF_NAME)
                data.setdefault(CONF_PROFILE_WEEKEND, "")
                data.setdefault(CONF_SELL_PRICE_SENSOR, "")
                data.setdefault(CONF_PV_POWER_SENSOR, "")
                return self.async_create_entry(title=title, data={}, options=data)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}, with_name=True), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AccuPlannerOptionsFlow()


SECTION_PLAN = "plan"
SECTION_SOLAR = "solar"
SOLAR_ENTITY_KEYS = (CONF_SOLAR_PV_ENTITY,)
GRID_KEYS = (CONF_SOLAR_EXPORT_ENTITY, CONF_SOLAR_IMPORT_ENTITY)
BATTERY_ENTITY_KEYS = (CONF_CTRL_MODE_ENTITY, CONF_CTRL_POWER_ENTITY)


def _control_schema(cur: dict[str, Any]) -> vol.Schema:
    d = {**DEFAULTS, **cur}
    power_sensor = EntitySelector(EntitySelectorConfig(domain="sensor"))
    plan = vol.Schema(
        {
            vol.Required(CONF_PLAN_ENABLED, default=d[CONF_PLAN_ENABLED]): BooleanSelector(),
            vol.Required(CONF_CTRL_CHARGE_MAX, default=d[CONF_CTRL_CHARGE_MAX]): BooleanSelector(),
            vol.Required(CONF_CTRL_POWER_DISCHARGE, default=d[CONF_CTRL_POWER_DISCHARGE]): _num(-50000, 50000, 10, "W"),
            vol.Required(CONF_CTRL_SAFE_ON_OFF, default=d[CONF_CTRL_SAFE_ON_OFF]): BooleanSelector(),
        }
    )
    solar = vol.Schema(
        {
            vol.Required(CONF_SOLAR_ENABLED, default=d[CONF_SOLAR_ENABLED]): BooleanSelector(),
            vol.Optional(
                CONF_SOLAR_PV_ENTITY, description={"suggested_value": d[CONF_SOLAR_PV_ENTITY] or None}
            ): power_sensor,
            vol.Required(CONF_SOLAR_MIN_POWER, default=d[CONF_SOLAR_MIN_POWER]): _num(0, 5000, 10, "W"),
            vol.Required(CONF_SOLAR_MAX_POWER, default=d[CONF_SOLAR_MAX_POWER]): _num(100, 50000, 100, "W"),
            vol.Required(CONF_SOLAR_BELOW_DISCHARGE, default=d[CONF_SOLAR_BELOW_DISCHARGE]): BooleanSelector(),
        }
    )
    return vol.Schema(
        {
            vol.Required(SECTION_PLAN): section(plan, {"collapsed": not d[CONF_PLAN_ENABLED]}),
            vol.Required(SECTION_SOLAR): section(solar, {"collapsed": not d[CONF_SOLAR_ENABLED]}),
        }
    )


def _flatten_control(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input.get(SECTION_PLAN, {}))
    solar = user_input.get(SECTION_SOLAR, {})
    data.update(solar)
    for key in SOLAR_ENTITY_KEYS:
        data[key] = solar.get(key, "")
    return data


def _grid_schema(cur: dict[str, Any]) -> vol.Schema:
    """Grid & Battery settings: battery control and grid meter."""
    d = {**DEFAULTS, **cur}
    sensor = EntitySelector(EntitySelectorConfig(domain="sensor"))

    def suggested(key: str) -> dict[str, Any]:
        return {"suggested_value": d[key] or None}

    return vol.Schema(
        {
            vol.Optional(CONF_CTRL_MODE_ENTITY, description=suggested(CONF_CTRL_MODE_ENTITY)): EntitySelector(
                EntitySelectorConfig(domain=["select", "input_select"])
            ),
            vol.Required(CONF_CTRL_OPT_CHARGE, default=d[CONF_CTRL_OPT_CHARGE]): TextSelector(),
            vol.Required(CONF_CTRL_OPT_DISCHARGE, default=d[CONF_CTRL_OPT_DISCHARGE]): TextSelector(),
            vol.Optional(CONF_CTRL_POWER_ENTITY, description=suggested(CONF_CTRL_POWER_ENTITY)): EntitySelector(
                EntitySelectorConfig(domain=["number", "input_number"])
            ),
            vol.Required(CONF_CAPACITY_SENSOR, default=d[CONF_CAPACITY_SENSOR]): sensor,
            vol.Required(CONF_SOC_SENSOR, default=d[CONF_SOC_SENSOR]): sensor,
            vol.Optional(CONF_SOLAR_EXPORT_ENTITY, description=suggested(CONF_SOLAR_EXPORT_ENTITY)): sensor,
            vol.Optional(CONF_SOLAR_IMPORT_ENTITY, description=suggested(CONF_SOLAR_IMPORT_ENTITY)): sensor,
        }
    )


def _discharge_schema(cur: dict[str, Any]) -> vol.Schema:
    d = {**DEFAULTS, **cur}
    return vol.Schema(
        {
            vol.Required(CONF_DIS_ENABLED, default=d[CONF_DIS_ENABLED]): BooleanSelector(),
            vol.Required(CONF_DIS_MIN_SOC, default=d[CONF_DIS_MIN_SOC]): _num(0, 100, 1, "%"),
            vol.Required(CONF_DIS_MIN_POWER, default=d[CONF_DIS_MIN_POWER]): _num(0, 5000, 10, "W"),
            vol.Required(CONF_DIS_MAX_POWER, default=d[CONF_DIS_MAX_POWER]): _num(100, 50000, 100, "W"),
            vol.Required(CONF_DIS_HYSTERESIS, default=d[CONF_DIS_HYSTERESIS]): _num(0, 10, 0.1, "ct/kWh"),
            vol.Required(CONF_DIS_SELL_ENABLED, default=d[CONF_DIS_SELL_ENABLED]): BooleanSelector(),
            vol.Required(CONF_DIS_SELL_POWER, default=d[CONF_DIS_SELL_POWER]): _num(100, 50000, 100, "W"),
        }
    )


class AccuPlannerOptionsFlow(OptionsFlow):
    """Options: Planning, Grid & Battery settings, Battery charge settings, Battery discharge settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "planning": "Planning (prices, consumption, costs, selling)",
                "grid": "Grid & Battery settings (battery entities and grid meter)",
                "control": "Battery charge settings (charge by plan and on solar surplus)",
                "discharge": "Battery discharge settings (discharge to consumption and sell)",
            },
        )

    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_planning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                data = {k: v for k, v in {**self.config_entry.options, **user_input}.items() if k not in OBSOLETE_KEYS}
                for key in (CONF_PROFILE_WEEKEND, CONF_SELL_PRICE_SENSOR, CONF_PV_POWER_SENSOR):
                    data[key] = user_input.get(key, "")
                return self.async_create_entry(data=data)
        current = user_input or self._current()
        return self.async_show_form(step_id="planning", data_schema=_schema(current, with_name=False), errors=errors)

    async def async_step_grid(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Grid & Battery settings: battery entities (mode, options, power, capacity, SOC) and grid meter."""
        if user_input is not None:
            data = {**self.config_entry.options, **user_input}
            for key in (*BATTERY_ENTITY_KEYS, *GRID_KEYS):
                data[key] = user_input.get(key, "")
            return self.async_create_entry(data=data)
        return self.async_show_form(step_id="grid", data_schema=_grid_schema(self._current()))

    def _has_battery_entities(self) -> bool:
        c = {**DEFAULTS, **self._current()}
        return any(c.get(k) for k in BATTERY_ENTITY_KEYS)

    def _has_grid_meter(self) -> bool:
        c = {**DEFAULTS, **self._current()}
        return all(c.get(k) for k in GRID_KEYS)

    def _placeholders(self) -> dict[str, str]:
        c = {**DEFAULTS, **self._current()}
        return {
            "mode_entity": c.get(CONF_CTRL_MODE_ENTITY) or "—",
            "discharge_option": c.get(CONF_CTRL_OPT_DISCHARGE) or "—",
            "power_entity": c.get(CONF_CTRL_POWER_ENTITY) or "—",
            "export_entity": c.get(CONF_SOLAR_EXPORT_ENTITY) or "—",
            "import_entity": c.get(CONF_SOLAR_IMPORT_ENTITY) or "—",
        }

    async def async_step_discharge(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Battery discharge settings: discharge to consumption above the discharge price, sell above the sell price."""
        errors: dict[str, str] = {}
        current = self._current()
        if user_input is not None:
            if user_input.get(CONF_DIS_ENABLED):
                if not self._has_battery_entities():
                    errors["base"] = "no_control_entities"
                elif not self._has_grid_meter():
                    errors["base"] = "needs_grid_sensors"
            if not errors:
                return self.async_create_entry(data={**self.config_entry.options, **user_input})
        return self.async_show_form(
            step_id="discharge",
            data_schema=_discharge_schema(user_input or current),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Battery charge settings: charge by plan and on solar surplus."""
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = _flatten_control(user_input)
            if (flat.get(CONF_PLAN_ENABLED) or flat.get(CONF_SOLAR_ENABLED)) and not self._has_battery_entities():
                errors["base"] = "no_control_entities"
            elif flat.get(CONF_SOLAR_ENABLED) and not self._has_grid_meter():
                errors["base"] = "needs_grid_sensors"
            else:
                data = {**self.config_entry.options, **flat}
                return self.async_create_entry(data={k: v for k, v in data.items() if k not in OBSOLETE_KEYS})
        current = _flatten_control(user_input) if user_input else self._current()
        return self.async_show_form(
            step_id="control",
            data_schema=_control_schema(current),
            errors=errors,
            description_placeholders=self._placeholders(),
        )
