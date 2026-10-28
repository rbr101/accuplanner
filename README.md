# Accu Planner

Home Assistant custom integration that plans and executes charging and discharging of a home battery based on dynamic quarter-hour electricity prices.

![Charge schedule card](https://raw.githubusercontent.com/rbr101/accuplanner/main/images/charge-schedule-card.png)

## Installation

**Manual:** copy `custom_components/accu_planner` to `/config/custom_components/` and restart Home Assistant.

**HACS:** add `https://github.com/rbr101/accuplanner` as a *custom repository* (type *Integration*) and install *Accu Planner*.

Then: **Settings → Devices & services → Add integration → Accu Planner**.
All settings can be changed later via **Configure** on the integration. After updating the integration, restart Home Assistant (Python code is only loaded at startup) and refresh the browser (Ctrl+F5).

## Settings

**Configure** opens a menu with four sections.

### Planning

| Setting | Description |
|---|---|
| Purchase price sensor | Quarter-hour or hourly purchase prices in the attributes (Zonneplan, Nordpool, EnergyZero, Tibber, …). Used for charging cost and for the savings of self-consumption from the battery |
| Sell price sensor (optional) | Feed-in prices in the same format. Empty = the purchase price is used |
| PV power sensor (optional) | For the stored energy cost: the share of charging covered by PV counts as solar energy. Empty = every SOC increase is priced at the purchase price |
| Consumption profile | `07-10=1.5; 17:30-19=2.5; 22-07=1.0; *=0.4` (kW, `*` = rest of the day). Optional separate weekend profile |
| Charge efficiency | Share of grid energy that ends up in the battery (default 85 %) |
| Wear cost & discharge margin | Battery wear plus extra margin per kWh discharged (ct, default 5). Added to the discharge price, the sell price and the savings after wear |
| Solar energy cost | Cost of an SOC increase without charging from the grid (ct, default 0) |
| Min/max SOC, max charge/discharge power | Battery limits |
| Use stored energy cost as discharge floor | On: never discharge below the stored energy cost + wear cost & discharge margin (default off) |
| Sell profit percentage | Sell price = (stored energy cost + wear cost & discharge margin) × (1 + percentage). The stored energy cost already includes the charge efficiency |

### Grid & Battery settings

The battery entities and the grid meter (e.g. P1). Used by Battery charge settings and Battery discharge settings; the mode is set first, then the power.

| Setting | Default |
|---|---|
| Battery total capacity sensor | `sensor.buiten_anker_solix_battery_capacity` (kWh or Wh) |
| Battery state of charge sensor | `sensor.buiten_anker_solix_soc` (%) |
| Mode entity (select) | `select.buiten_anker_solix_grid_flow` |
| Option when charging / when discharging | `charge` / `discharge` |
| Power entity (number) | `number.buiten_anker_solix_target_grid_power` |
| Grid export sensor (power production) | `sensor.power_production` (W or kW) |
| Grid import sensor (power consumption) | `sensor.power_consumption` (W or kW) |

### Battery charge settings

Executed while the **Execute battery plan** switch is on (its state is kept across restarts).

**Charge by plan**

| Setting | Default |
|---|---|
| Charge by plan | on (off = the planner's charge windows are not executed) |
| Always charge at max charge power | off (= the planned charge power of the quarter) |
| Power after charging | 0 W — after a charge window the battery returns once to the discharge option with this power |
| Return to discharge setting when switched off | on |

**Charge on solar surplus** — outside charge windows the charge power follows the grid export, like the original Node-RED flow: measure export − import every 3 s (only while the PV sensor produces > 1 W), adjust every 15 s by 0.6 × the average (max +400 / −1000 W per step), limited between 0 and the maximum, 0 W below the minimum (200 W). By default only while the price is below the discharge price. Without sun `charge` with 0 W is sent (hold the battery).

| Setting | Default |
|---|---|
| Charge on solar surplus | off |
| PV production sensor | `sensor.growatt_solar_ac_power` |
| Minimum / maximum charge power | 200 W / 3500 W |
| Only when the price is below the discharge price | on |

### Battery discharge settings

With its own on/off switch (default off). Works like the original Node-RED discharge flow and uses the battery control and grid meter from Grid & Battery settings; the form shows which entities:

- **price > discharge price** and SOC above the minimum: mode `discharge`, the power follows the consumption so that grid import goes towards 0 W (measure import − export every 3 s, adjust every 20 s by 0.25 × the average, limited 0 – max, 0 W below 100 W, rounded to 50 W, only sent on ≥ 100 W change; after 5 cycles against a limit it takes over the current battery setting). On start it continues from the current discharge power (not from 0 W) and after a short interruption (< 10 min) from its last value;
- **price > sell price** (while discharging): discharge at full power (default 3500 W) = export. The charge plan follows the same rule. If the sell price is below the discharge price, every discharge quarter is also a sell quarter;
- **price ≤ discharge price** (and no solar surplus charging): power 0 W.

| Setting | Default |
|---|---|
| Control discharging | off |
| Do not discharge below SOC | 2 % |
| Minimum / maximum discharge power | 100 W / 3500 W |
| Hysteresis | 1 ct (once discharging, only stop when the price drops this much below the discharge price) |
| Sell at full power above the sell price | on |
| Power when selling | 3500 W |

**Priority:** charge window of the planner → discharging (above the discharge price) → solar surplus charging (below the discharge price) → otherwise return once to the discharge setting.

Execution: after every new plan, every quarter and every minute the desired state is compared with the current one; only when it differs the mode and then the power are set. If the plan is older than 20 minutes or failed, the battery returns to the discharge setting (never keep charging at high prices). The switch attributes show the target mode (`target_mode`: `charge`, `solar`, `dis`, `dis_hold`, `dis_sell`, `released`), the last command, errors (`last_error`) and the regulators (`solar_status`, `discharge_status`).

## Sensors

| Sensor | Content |
|---|---|
| Mode | `charge` / `discharge` / `idle` according to the plan. Attributes: ranges, report, plain text (`text`) and per-quarter series (`series`) |
| Charge power | Planned charge power now (W) |
| Discharge power | Expected discharge power now (W) |
| Discharge price | Value of 1 kWh in the battery (attribute `energy_value`) + wear cost & discharge margin (€/kWh) for the **current** quarter: above this price the battery discharges. The threshold differs per quarter (dashed line in the chart, `series.threshold`) |
| Sell price | (Stored energy cost incl. efficiency + wear cost & discharge margin) × (1 + profit percentage) |
| Stored energy cost | Weighted average price per kWh **in** the battery (incl. efficiency losses). Attribute `source`: history / live / manual / estimate |
| SOC end of plan | Expected SOC at the end of the known prices |
| Expected savings | What the plan yields until the end of the known prices (€): avoided purchase + export − charging cost, corrected for more/less stored energy at the end. Attribute `after_wear` |
| Total savings | Running total of the actual savings (€, `state_class: total` → long-term statistics). Attributes: `today`, `yesterday`, `this_week`, `previous_week`, `this_month`, `previous_month` |

## Dashboard cards

The integration ships two cards (no extra installation). After installing/restarting, refresh the browser once.

```yaml
type: custom:accu-planner-card
# optional (without entity the card finds the Accu Planner Mode sensor itself):
entity: sensor.accu_planner_mode
title: Charge schedule
hours_back: 4        # hours back (max 24)
hours_ahead: 28      # hours ahead (fixed window; grey where prices are not yet known)
# or like the plotly-graph card:  hours_to_show: 32  +  time_offset: 28h  (= 4 back, 28 ahead)
height: 230
show_soc: true       # SOC as a blue line (right axis)
```

![Charge schedule card](https://raw.githubusercontent.com/rbr101/accuplanner/main/images/charge-schedule-card.png)

**Charge schedule** (`custom:accu-planner-card`): quarter-hour prices (green → red) in a fixed time window, up to 24 hours back with what was planned and the measured SOC (drawn fainter), charge quarters (●), discharge quarters (▼), export quarters ($) with a coloured strip below, the discharge price (dashed line), the expected SOC and a tooltip per quarter.

**Charge plan** (`custom:accu-planner-plan-card`): now, stored energy cost and value, charge/discharge/sell times, expected and actual savings, expected SOC per mode change, discharge price and sell price.

<img src="https://raw.githubusercontent.com/rbr101/accuplanner/main/images/charge-plan-card.png" alt="Charge plan card" width="500">

```yaml
type: custom:accu-planner-plan-card
# entity is optional, like the chart
max_steps: 12        # number of SOC changes before 'show all' appears
```

## Savings

Every kWh discharged from the battery yields (purchase price at that moment − cost of that kWh); the cost already includes charging cost and efficiency. Daily totals are stored and, on first start, filled in from the recorder history (10 days by default).

Per period, without extra sensors, with the standard HA cards on **Total savings** (check the entity ID under Settings → Entities):

```yaml
type: grid
columns: 3
cards:
  - type: statistic
    entity: sensor.accu_planner_total_savings
    name: Today
    stat_type: change
    period: { calendar: { period: day } }
  - type: statistic
    entity: sensor.accu_planner_total_savings
    name: This week
    stat_type: change
    period: { calendar: { period: week } }
  - type: statistic
    entity: sensor.accu_planner_total_savings
    name: This month
    stat_type: change
    period: { calendar: { period: month } }
  - type: statistic
    entity: sensor.accu_planner_total_savings
    name: Yesterday
    stat_type: change
    period: { calendar: { period: day, offset: -1 } }
  - type: statistic
    entity: sensor.accu_planner_total_savings
    name: Last week
    stat_type: change
    period: { calendar: { period: week, offset: -1 } }
  - type: statistic
    entity: sensor.accu_planner_total_savings
    name: Last month
    stat_type: change
    period: { calendar: { period: month, offset: -1 } }
```

Bar chart per day (or `period: week` / `month`):

```yaml
type: statistics-graph
entities: [sensor.accu_planner_total_savings]
stat_types: [change]
period: day
chart_type: bar
days_to_show: 30
```

HA statistics start when the sensor is created; the attributes also contain the days filled in from history.

## Stored energy cost

On every SOC increase the energy is added at the purchase price of that moment ÷ efficiency (with a PV sensor: the solar share at the solar energy cost). Discharging does not change the average. If no stored cost exists yet, the integration calculates it on start from the recorder history of the last 7 days (SOC, purchase price and PV).

## Service

`accu_planner.set_cost_price` with `cost_ct: 21.5` sets the stored energy cost manually.

## How the planning works

Dynamic programming over the battery content (steps of 0.05 kWh) until the end of the known prices. For every quarter the planner chooses charge, idle or discharge so that the total energy bill is minimal:

- charging costs purchase price ÷ efficiency per kWh in the battery;
- discharging saves the purchase price (self-consumption) or yields the sell price (export); every kWh discharged costs the wear cost & discharge margin;
- energy still in the battery at the end is worth what recharging in the cheapest quarters costs.

Because the whole horizon is considered at once, the battery only charges what is needed if cheaper charging is possible later (e.g. at night or in the midday dip). The discharge price is the value of the next kWh on the planned path + wear cost & discharge margin.

## Logo

The integration has its own icon and logo in `brand/` (light and dark). Home Assistant shows them from version 2026.3.
