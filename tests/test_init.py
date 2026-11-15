"""Home Assistant tests (needs pytest-homeassistant-custom-component)."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

DOMAIN = "accu_planner"
PRICE = "sensor.price"


def _forecast(values):
    start = dt_util.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return [
        {"time": (start + timedelta(minutes=15 * i)).isoformat(), "price": v} for i, v in enumerate(values)
    ]


PRICES = _forecast([0.30] * 4 + [0.25] * 8 + [0.60] * 8 + [0.30] * 30 + [0.55] * 8 + [0.28] * 30)

OPTIONS = {
    "price_sensor": PRICE,
    "capacity_sensor": "sensor.capacity",
    "soc_sensor": "sensor.soc",
    "control_mode_entity": "input_select.battery_mode",
    "control_power_entity": "input_number.battery_power",
    "discharge_car_entity": "",
}


async def _states(hass, price_attrs=None):
    hass.states.async_set(PRICE, "0.3", price_attrs if price_attrs is not None else {"prices_today": PRICES})
    hass.states.async_set("sensor.capacity", "10", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.soc", "50", {"unit_of_measurement": "%"})


async def _setup(hass, options=None, minor_version=4):
    await _states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Accu Planner", data={}, options=options or OPTIONS, version=1,
        minor_version=minor_version,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(hass):
    await _states(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Thuisaccu", "price_sensor": PRICE, "profile": "*=0.5"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Thuisaccu"
    assert result["options"]["price_sensor"] == PRICE


async def test_config_flow_rejects_bad_profile(hass):
    await _states(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", "price_sensor": PRICE, "profile": "nonsense"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"profile": "invalid_profile"}


async def test_setup_creates_entities(hass):
    entry = await _setup(hass)
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    domains = {e.domain for e in entities}
    assert domains == {"sensor", "switch", "number"}
    mode = next(e.entity_id for e in entities if e.unique_id.endswith("_mode"))
    assert hass.states.get(mode).state in ("charge", "discharge", "idle")
    price = next(e.entity_id for e in entities if e.unique_id.endswith("_discharge_price"))
    assert float(hass.states.get(price).state) > 0


async def test_options_menu(hass):
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert len(result["menu_options"]) == 4


async def test_setting_switch_without_reload(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    ent_reg = er.async_get(hass)
    switch = ent_reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_discharge_enabled")
    assert hass.states.get(switch).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on"
    assert entry.options["discharge_enabled"] is True
    assert entry.runtime_data is coordinator  # no reload


async def test_reaction_speed_number(hass):
    entry = await _setup(hass)
    number = er.async_get(hass).async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_discharge_speed")
    await hass.services.async_call("number", "set_value", {"entity_id": number, "value": 8}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(number).state == "8.0"
    assert entry.options["discharge_speed"] == 8


async def test_keeps_working_without_price_sensor(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    hass.states.async_set(PRICE, "unavailable", {})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert coordinator.data["price_source"] == "stored"
    assert coordinator.data["report"][0]["kind"] == "warning"


async def test_migration_removes_cost_floor(hass):
    entry = await _setup(hass, options={**OPTIONS, "cost_floor": True}, minor_version=3)
    assert entry.minor_version == 4
    assert "cost_floor" not in entry.options


async def test_unload(hass):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED
