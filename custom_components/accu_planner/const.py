"""Constanten voor Accu Planner."""

DOMAIN = "accu_planner"

CONF_PRICE_SENSOR = "price_sensor"  # inkoop
CONF_SELL_PRICE_SENSOR = "sell_price_sensor"  # verkoop (optioneel)
CONF_CAPACITY_SENSOR = "capacity_sensor"
CONF_SOC_SENSOR = "soc_sensor"
CONF_PV_POWER_SENSOR = "pv_power_sensor"  # optioneel, W of kW
CONF_PROFILE = "profile"
CONF_PROFILE_WEEKEND = "profile_weekend"
CONF_EFFICIENCY = "efficiency"
CONF_DISCHARGE_MARGIN = "discharge_margin"  # vervallen (1.9): opgeteld bij wear_cost
CONF_WEAR_COST = "wear_cost"
CONF_SOLAR_COST = "solar_cost"
CONF_CHARGE_POWER = "charge_power"
CONF_DISCHARGE_POWER = "discharge_power"
CONF_MIN_SOC = "min_soc"
CONF_MAX_SOC = "max_soc"
CONF_COST_FLOOR = "cost_floor"
CONF_ALLOW_EXPORT = "allow_export"  # obsolete (1.11): derived from discharge sell setting
CONF_PROFIT_PCT = "profit_pct"
CONF_UPDATE_INTERVAL = "update_interval"

# Aansturing (uitvoeren van het plan)
CONF_CTRL_MODE_ENTITY = "control_mode_entity"  # select / input_select
CONF_CTRL_OPT_CHARGE = "control_option_charge"
CONF_CTRL_OPT_DISCHARGE = "control_option_discharge"
CONF_CTRL_OPT_IDLE = "control_option_idle"  # vervallen (1.9)
CONF_CTRL_POWER_ENTITY = "control_power_entity"  # number / input_number
CONF_CTRL_CHARGE_MAX = "control_charge_at_max"
CONF_CTRL_POWER_DISCHARGE = "control_power_discharge"
CONF_CTRL_POWER_IDLE = "control_power_idle"  # vervallen (1.9)
CONF_CTRL_SAFE_ON_OFF = "control_safe_on_off"
CONF_PLAN_ENABLED = "plan_charge_enabled"  # laden volgens plan aan/uit
CONF_CTRL_DISCHARGE = "control_discharge"  # vervallen (1.9): ontladen via Discharge settings
OBSOLETE_KEYS = (CONF_DISCHARGE_MARGIN, CONF_CTRL_OPT_IDLE, CONF_CTRL_POWER_IDLE, CONF_CTRL_DISCHARGE, CONF_ALLOW_EXPORT)

# Laden bij zonne-overschot
CONF_SOLAR_ENABLED = "solar_enabled"
CONF_SOLAR_PV_ENTITY = "solar_pv_entity"  # PV-productie (W/kW): regelaar alleen actief als > 1 W
CONF_SOLAR_EXPORT_ENTITY = "solar_export_entity"  # teruglevering aan het net (W/kW)
CONF_SOLAR_IMPORT_ENTITY = "solar_import_entity"  # afname van het net (W/kW)
CONF_SOLAR_MIN_POWER = "solar_min_power"
CONF_SOLAR_MAX_POWER = "solar_max_power"
CONF_SOLAR_BELOW_DISCHARGE = "solar_below_discharge_price"

# Ontladen (Discharge settings)
CONF_DIS_ENABLED = "discharge_enabled"
CONF_DIS_MIN_SOC = "discharge_min_soc"
CONF_DIS_MIN_POWER = "discharge_min_power"
CONF_DIS_MAX_POWER = "discharge_max_power"
CONF_DIS_SELL_ENABLED = "discharge_sell_enabled"
CONF_DIS_SELL_POWER = "discharge_sell_power"
CONF_DIS_HYSTERESIS = "discharge_hysteresis"  # ct: blijven ontladen tot prijs < drempel - hysterese

DEFAULTS = {
    CONF_PRICE_SENSOR: "sensor.zonneplan_current_quarter_hourly_electricity_tariff",
    CONF_SELL_PRICE_SENSOR: "",
    CONF_CAPACITY_SENSOR: "sensor.buiten_anker_solix_battery_capacity",
    CONF_SOC_SENSOR: "sensor.buiten_anker_solix_soc",
    CONF_PV_POWER_SENSOR: "",
    CONF_PROFILE: "07-10=1.5; 17-19=2.5; 19-22=1.5; 22-07=1.0; *=0.4",
    CONF_PROFILE_WEEKEND: "",
    CONF_EFFICIENCY: 85,
    CONF_WEAR_COST: 5,  # slijtage & ontlaadmarge samen (ct/kWh)
    CONF_SOLAR_COST: 0,
    CONF_CHARGE_POWER: 3300,
    CONF_DISCHARGE_POWER: 3500,
    CONF_MIN_SOC: 5,
    CONF_MAX_SOC: 100,
    CONF_COST_FLOOR: False,
    CONF_PROFIT_PCT: 20,
    CONF_UPDATE_INTERVAL: 5,
    CONF_CTRL_MODE_ENTITY: "select.buiten_anker_solix_grid_flow",
    CONF_CTRL_OPT_CHARGE: "charge",
    CONF_CTRL_OPT_DISCHARGE: "discharge",
    CONF_CTRL_POWER_ENTITY: "number.buiten_anker_solix_target_grid_power",
    CONF_CTRL_CHARGE_MAX: False,
    CONF_CTRL_POWER_DISCHARGE: 0,
    CONF_CTRL_SAFE_ON_OFF: True,
    CONF_PLAN_ENABLED: True,
    CONF_SOLAR_ENABLED: False,
    CONF_SOLAR_PV_ENTITY: "sensor.growatt_solar_ac_power",
    CONF_SOLAR_EXPORT_ENTITY: "sensor.power_production",
    CONF_SOLAR_IMPORT_ENTITY: "sensor.power_consumption",
    CONF_SOLAR_MIN_POWER: 200,
    CONF_SOLAR_MAX_POWER: 3500,
    CONF_SOLAR_BELOW_DISCHARGE: True,
    CONF_DIS_ENABLED: False,
    CONF_DIS_MIN_SOC: 2,
    CONF_DIS_MIN_POWER: 100,
    CONF_DIS_MAX_POWER: 3500,
    CONF_DIS_SELL_ENABLED: True,
    CONF_DIS_SELL_POWER: 3500,
    CONF_DIS_HYSTERESIS: 1.0,
}

SERVICE_SET_COST_PRICE = "set_cost_price"
ATTR_COST_CT = "cost_ct"

STORAGE_VERSION = 1
