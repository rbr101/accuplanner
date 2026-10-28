"""Sensoren voor Accu Planner."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AccuPlannerConfigEntry, AccuPlannerCoordinator

EUR_KWH = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"


@dataclass(frozen=True, kw_only=True)
class AccuSensorDescription(SensorEntityDescription):
    """Beschrijving met value- en attribuutfunctie."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _plan_attrs(d: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "next_action", "prices_known_until", "charge_ranges", "discharge_ranges",
        "planned_grid_charge_kwh", "planned_charge_cost", "planned_discharge_kwh", "planned_export_kwh",
        "planned_revenue", "price_now", "sell_price_now", "energy_value", "discharge_price", "avg_cost",
        "sell_price", "soc", "soc_min", "charge_power", "discharge_power", "report", "soc_steps", "series", "text",
    )
    return {k: d.get(k) for k in keys}


SENSORS: tuple[AccuSensorDescription, ...] = (
    AccuSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=["charge", "discharge", "idle"],
        value_fn=lambda d: d["mode"],
        attrs_fn=_plan_attrs,
    ),
    AccuSensorDescription(
        key="charge_power",
        translation_key="charge_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["charge_power"],
    ),
    AccuSensorDescription(
        key="discharge_power",
        translation_key="discharge_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["discharge_power"],
    ),
    AccuSensorDescription(
        key="discharge_price",
        translation_key="discharge_price",
        native_unit_of_measurement=EUR_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d["discharge_price"],
        attrs_fn=lambda d: {"energy_value": d["energy_value"], "cost": d["cost"]},
    ),
    AccuSensorDescription(
        key="sell_price",
        translation_key="sell_price",
        native_unit_of_measurement=EUR_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d["sell_price"],
        attrs_fn=lambda d: {
            "profit_pct": d["profit_pct"], "allow_export": d["allow_export"], "cost": d["cost"], "base": d["sell_base"],
        },
    ),
    AccuSensorDescription(
        key="avg_cost",
        translation_key="avg_cost",
        native_unit_of_measurement=EUR_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d["avg_cost"],
        attrs_fn=lambda d: {"stored_kwh": d["stored_kwh"], "estimated": d["avg_cost_estimated"], "source": d["avg_cost_source"]},
    ),
    AccuSensorDescription(
        key="expected_savings",
        translation_key="expected_savings",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: d["expected_savings"],
        attrs_fn=lambda d: {
            "after_wear": d["expected_savings_after_wear"],
            "until": d["prices_known_until"],
            "revenue": d["planned_revenue"],
            "charge_cost": d["planned_charge_cost"],
        },
    ),
    AccuSensorDescription(
        key="savings_total",
        translation_key="savings_total",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: d["savings_total"],
        attrs_fn=lambda d: {
            **{f"{k}": v["eur"] for k, v in d["savings"].items() if isinstance(v, dict)},
            "kwh_from_battery": {k: v["kwh"] for k, v in d["savings"].items() if isinstance(v, dict)},
            "since": d["savings"]["first_day"],
        },
    ),
    AccuSensorDescription(
        key="soc_end",
        translation_key="soc_end",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d["soc_end"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: AccuPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(AccuSensor(coordinator, desc) for desc in SENSORS)


class AccuSensor(CoordinatorEntity[AccuPlannerCoordinator], SensorEntity):
    """Een sensor die een waarde uit de planning toont."""

    _attr_has_entity_name = True
    # grote attributen niet in de recorder (16 kB-grens)
    _unrecorded_attributes = frozenset(
        {"series", "text", "report", "soc_steps", "charge_ranges", "discharge_ranges"}
    )
    entity_description: AccuSensorDescription

    def __init__(self, coordinator: AccuPlannerCoordinator, description: AccuSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Accu Planner",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
