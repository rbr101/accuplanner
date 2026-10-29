"""Laat de accu het plan uitvoeren via een modus-select en een vermogens-number."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHARGE_POWER,
    CONF_CTRL_CHARGE_MAX,
    CONF_CTRL_MODE_ENTITY,
    CONF_CTRL_OPT_CHARGE,
    CONF_CTRL_OPT_DISCHARGE,
    CONF_CTRL_POWER_DISCHARGE,
    CONF_CTRL_POWER_ENTITY,
    CONF_CTRL_SAFE_ON_OFF,
    CONF_DIS_CAR_BLOCK,
    CONF_DIS_CAR_ENTITY,
    CONF_DIS_ENABLED,
    CONF_DIS_SPEED,
    CONF_DIS_HYSTERESIS,
    CONF_DIS_MAX_POWER,
    CONF_DIS_MIN_POWER,
    CONF_DIS_MIN_SOC,
    CONF_DIS_SELL_ENABLED,
    CONF_DIS_SELL_POWER,
    CONF_MAX_SOC,
    CONF_PLAN_ENABLED,
    CONF_SOC_SENSOR,
    CONF_SOLAR_BELOW_DISCHARGE,
    CONF_SOLAR_ENABLED,
    CONF_SOLAR_EXPORT_ENTITY,
    CONF_SOLAR_IMPORT_ENTITY,
    CONF_SOLAR_MAX_POWER,
    CONF_SOLAR_MIN_POWER,
    CONF_SOLAR_PV_ENTITY,
)
from .solar import (
    CONTROL_INTERVAL_S,
    DIS_MIN_CHANGE_W,
    DIS_SAMPLE_INTERVAL_S,
    MIN_CHANGE_W,
    SAMPLE_INTERVAL_S,
    DischargeRegulator,
    SurplusRegulator,
    discharge_speed,
)

if TYPE_CHECKING:
    from .coordinator import AccuPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

CHECK_INTERVAL = timedelta(minutes=1)
STALE_AFTER = timedelta(minutes=20)
DIS_KEEP_TARGET_S = 600  # regelaar onthoudt zijn ontlaadvermogen zo lang na een onderbreking  # oudere planning -> veilige stand (ontladen)
MODE_SETTLE_S = 0.5  # pauze tussen modus en vermogen (Node-RED gebruikte 100 ms)


class PlanExecutor:
    """Zet elke minuut (en na elke nieuwe planning) de accu in de gewenste stand.

    Volgorde van voorrang:
      1. laadvenster van de planner  -> laden (gepland of max vermogen)
      1b. ontladen (Discharge settings aan, prijs > ontlaadprijs, SOC > min) -> ontladen
         met het vermogen van de ontlaadregelaar; prijs > verkoopprijs -> max (verkopen).
         Prijs <= ontlaadprijs en geen zonne-overschot -> vermogen 0 W
      2. zonne-overschot (indien aan, prijs < ontlaadprijs) -> laden met het vermogen
         van de overschotregelaar (0 W = vasthouden)
      3. anders: met 'Ontladen aansturen' uit één keer terug naar de ontlaadstand en
         daarna niets; met 'Ontladen aansturen' aan het plan (ontladen / vasthouden).
    Er wordt alleen geschreven als de huidige stand afwijkt.
    """

    def __init__(self, hass: HomeAssistant, coordinator: AccuPlannerCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.enabled = False
        self.last_command: str | None = None
        self.last_command_at: datetime | None = None
        self.last_error: str | None = None
        self.target: str | None = None
        self._last_good: datetime | None = None
        self._lock = asyncio.Lock()
        self._unsubs: list = []
        self._state_listeners: list = []
        self.solar = SurplusRegulator(
            min_out=float(self._conf(CONF_SOLAR_MIN_POWER) or 200),
            max_out=float(self._conf(CONF_SOLAR_MAX_POWER) or 3500),
        )
        self.solar_status: str | None = None
        self.discharge = DischargeRegulator(
            min_out=float(self._conf(CONF_DIS_MIN_POWER) or 100),
            max_out=float(self._conf(CONF_DIS_MAX_POWER) or 3500),
        )
        self.discharge_status: str | None = None
        self._dis_last_active = 0.0  # time.monotonic() van de laatste keer ontladen naar verbruik

    # ------------------------------------------------------------ levenscyclus
    @callback
    def async_start(self) -> None:
        self._unsubs.append(self.coordinator.async_add_listener(self._on_update))
        self._unsubs.append(
            async_track_time_interval(self.hass, self._minute, CHECK_INTERVAL)
        )
        if self.coordinator.last_update_success and self.coordinator.data:
            self._last_good = dt_util.utcnow()
        if self._conf(CONF_DIS_ENABLED):
            interval, self.discharge.gain = discharge_speed(self._conf(CONF_DIS_SPEED))
            self._unsubs.append(
                async_track_time_interval(self.hass, self._dis_sample, timedelta(seconds=DIS_SAMPLE_INTERVAL_S))
            )
            self._unsubs.append(
                async_track_time_interval(self.hass, self._dis_control, timedelta(seconds=interval))
            )
            if self._conf(CONF_DIS_CAR_BLOCK) and (car := self._conf(CONF_DIS_CAR_ENTITY)):
                self._unsubs.append(
                    async_track_state_change_event(self.hass, [car], self._car_changed)
                )
        if self._conf(CONF_SOLAR_ENABLED):
            self._unsubs.append(
                async_track_time_interval(self.hass, self._solar_sample, timedelta(seconds=SAMPLE_INTERVAL_S))
            )
            self._unsubs.append(
                async_track_time_interval(self.hass, self._solar_control, timedelta(seconds=CONTROL_INTERVAL_S))
            )

    async def async_settings_changed(self) -> None:
        """An on/off setting changed via a switch: restart the regulator timers and re-apply."""
        self.async_stop()
        if not self._conf(CONF_DIS_ENABLED):
            self.discharge.reset()
        if not self._conf(CONF_SOLAR_ENABLED):
            self.solar.reset()
        self.async_start()
        await self.coordinator.async_request_refresh()
        await self.async_apply("setting changed")
        self._notify()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def add_state_listener(self, cb) -> None:
        self._state_listeners.append(cb)

    def _notify(self) -> None:
        for cb in self._state_listeners:
            cb()

    @callback
    def _minute(self, _now=None) -> None:
        self._schedule("minute")

    @callback
    def _on_update(self) -> None:
        if self.coordinator.last_update_success and self.coordinator.data:
            self._last_good = dt_util.utcnow()
        self._schedule("new plan")

    @callback
    def _schedule(self, reason: str) -> None:
        if self.enabled:
            self.hass.async_create_task(self.async_apply(reason))

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            await self.async_apply("switched on")
        elif self._conf(CONF_CTRL_SAFE_ON_OFF) and (
            self.target in ("charge", "solar", "dis", "dis_sell", None)
        ):
            self.discharge.reset()
            self.solar.reset()
            await self.async_apply("switched off", force_mode="safe")
        self._notify()

    # ------------------------------------------------------------ zonne-overschot
    def _watts(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        try:
            v = float(str(st.state).replace(",", "."))
        except ValueError:
            return None
        return v * 1000 if (st.attributes.get("unit_of_measurement") or "").lower() == "kw" else v

    @callback
    def _solar_sample(self, _now=None) -> None:
        if not self.enabled:
            return
        pv = self._watts(self._conf(CONF_SOLAR_PV_ENTITY))
        if pv is not None and not pv > 1:
            self.solar_status = f"no PV production ({pv:g} W)"
            return
        exp = self._watts(self._conf(CONF_SOLAR_EXPORT_ENTITY))
        imp = self._watts(self._conf(CONF_SOLAR_IMPORT_ENTITY))
        if exp is None or imp is None:
            self.solar_status = "grid meter invalid (export/import)"
            return
        self.solar.add_sample(exp - imp)

    @callback
    def _solar_control(self, _now=None) -> None:
        if not self.enabled:
            self.solar.reset()
            return
        self.solar.step()
        if self.solar.last_error is not None:
            self.solar_status = (
                f"surplus {round(self.solar.last_error)} W, step {round(self.solar.last_step)} W, "
                f"target {round(self.solar.target)} W"
            )
        self._schedule("solar")

    # ------------------------------------------------------------ ontladen
    def _power_setting(self) -> float | None:
        st = self.hass.states.get(self._conf(CONF_CTRL_POWER_ENTITY) or "")
        try:
            return float(st.state) if st else None
        except ValueError:
            return None

    @callback
    def _dis_sample(self, _now=None) -> None:
        if not self.enabled:
            return
        exp = self._watts(self._conf(CONF_SOLAR_EXPORT_ENTITY))
        imp = self._watts(self._conf(CONF_SOLAR_IMPORT_ENTITY))
        if exp is None or imp is None:
            self.discharge_status = "grid meter invalid (export/import)"
            return
        self.discharge.add_sample(imp - exp)

    @callback
    def _dis_control(self, _now=None) -> None:
        if not self.enabled or self.target != "dis":
            # alleen regelen terwijl er naar verbruik ontladen wordt (doel blijft bewaard)
            self.discharge.samples.clear()
            if self.enabled and self._discharge_mode():
                self._schedule("discharge")
            return
        out = self.discharge.step(self._power_setting())
        if out is None:
            self.discharge_status = "no valid grid measurements"
            return
        self.discharge_status = (
            f"import {round(self.discharge.last_error)} W, target {round(self.discharge.target)} W, output {out} W"
        )
        self._schedule("discharge")

    def _seed_discharge(self, mode_entity: str | None) -> None:
        """Begin bij het huidige ontlaadvermogen (zoals Node-RED), niet bij 0 W.

        Alleen als de accu al op ontladen staat: in de laadstand is het getal een laadvermogen.
        """
        if mode_entity:
            st = self.hass.states.get(mode_entity)
            if st is None or st.state != self._conf(CONF_CTRL_OPT_DISCHARGE):
                return
        current = self._power_setting()
        if current is not None and current > 0:
            self.discharge.target = min(float(current), self.discharge.max_out)
            self.discharge_status = f"continuing from current power {round(current)} W"

    def _price(self, key: str) -> float | None:
        return (self.coordinator.data or {}).get(key)

    def _discharge_mode(self) -> str | None:
        """'dis' / 'dis_sell' als er ontladen moet worden, anders None."""
        if not self._conf(CONF_DIS_ENABLED):
            return None
        price, thr = self._price("price_now"), self._price("discharge_price")
        if price is None or thr is None:
            return None
        # hysterese: eenmaal aan het ontladen pas stoppen als de prijs duidelijk onder de drempel zakt
        hyst = float(self._conf(CONF_DIS_HYSTERESIS) or 0) / 100
        discharging = self.target in ("dis", "dis_sell")
        if not (price >= thr - hyst if discharging else price > thr):
            return None
        if self._car_charging():
            return None
        st = self.hass.states.get(self._conf(CONF_SOC_SENSOR) or "")
        try:
            if st is None or not float(st.state) > float(self._conf(CONF_DIS_MIN_SOC) or 0):
                return None
        except ValueError:
            return None
        sell = self._price("sell_price")
        selling = self.target == "dis_sell"
        if self._conf(CONF_DIS_SELL_ENABLED) and sell is not None and (
            price >= sell - hyst if selling else price > sell
        ):
            return "dis_sell"
        return "dis"

    @callback
    def _car_changed(self, _event) -> None:
        self._schedule("car charger")

    def _car_charging(self) -> bool:
        """The car charger draws power: do not discharge the battery into the car."""
        if not self._conf(CONF_DIS_CAR_BLOCK):
            return False
        power = self._watts(self._conf(CONF_DIS_CAR_ENTITY))
        if power is not None and power > 0:
            self.discharge_status = f"car charging ({round(power)} W): not discharging"
            return True
        return False

    def _solar_active(self) -> bool:
        """Zonne-overschot mag de accu sturen (buiten laadvensters)."""
        if not self._conf(CONF_SOLAR_ENABLED):
            return False
        data = self.coordinator.data or {}
        if self._conf(CONF_SOLAR_BELOW_DISCHARGE):
            price, thr = data.get("price_now"), data.get("discharge_price")
            if price is None or thr is None or not price < thr:
                return False
        return True

    def _soc_full(self) -> bool:
        st = self.hass.states.get(self._conf(CONF_SOC_SENSOR) or "")
        try:
            return st is not None and float(st.state) >= float(self._conf(CONF_MAX_SOC) or 100)
        except ValueError:
            return False

    # ------------------------------------------------------------ uitvoeren
    def _conf(self, key: str) -> Any:
        return self.coordinator.conf.get(key)

    def _target(self, mode: str) -> tuple[str | None, float | None]:
        data = self.coordinator.data or {}
        if mode == "charge":
            power = float(self._conf(CONF_CHARGE_POWER)) if self._conf(CONF_CTRL_CHARGE_MAX) else data.get("charge_power")
            return self._conf(CONF_CTRL_OPT_CHARGE), power
        if mode == "dis":
            return self._conf(CONF_CTRL_OPT_DISCHARGE), float(self.discharge.output)
        if mode == "dis_sell":
            return self._conf(CONF_CTRL_OPT_DISCHARGE), float(self._conf(CONF_DIS_SELL_POWER) or 3500)
        if mode == "dis_hold":
            return None, 0.0
        if mode == "solar":
            return self._conf(CONF_CTRL_OPT_CHARGE), 0.0 if self._soc_full() else float(self.solar.output)
        # discharge, de veilige stand en 'released' (terug na het laden)
        return self._conf(CONF_CTRL_OPT_DISCHARGE), float(self._conf(CONF_CTRL_POWER_DISCHARGE))

    def _current_mode(self) -> str:
        data = self.coordinator.data
        stale = (
            not data
            or not self.coordinator.last_update_success
            or self._last_good is None
            or dt_util.utcnow() - self._last_good > STALE_AFTER
        )
        return "safe" if stale else data["mode"]

    async def async_apply(self, reason: str, force_mode: str | None = None) -> None:
        if not self.enabled and force_mode is None:
            return
        mode_entity = self._conf(CONF_CTRL_MODE_ENTITY) or None
        power_entity = self._conf(CONF_CTRL_POWER_ENTITY) or None
        if not mode_entity and not power_entity:
            self._error("no mode or power entity configured (Configure → Grid & Battery settings)")
            return
        mode = force_mode or self._current_mode()
        if mode == "charge" and not self._conf(CONF_PLAN_ENABLED):
            mode = "idle"  # laden volgens plan staat uit: laadvensters negeren
        if mode not in ("charge", "safe") and force_mode is None:
            if dis := self._discharge_mode():
                mode = dis
            elif self._solar_active():
                mode = "solar"
            elif self._conf(CONF_DIS_ENABLED):
                mode = "dis_hold"  # prijs <= ontlaadprijs: niet ontladen (0 W)
        if mode == "charge":
            self.solar.reset()  # tijdens plan-laden begint de overschotregelaar daarna weer bij 0
        if mode == "dis":
            if self.target != "dis" and self.discharge.target <= 0:
                self._seed_discharge(mode_entity)
            self._dis_last_active = time.monotonic()
        elif mode in ("charge", "solar") or time.monotonic() - self._dis_last_active > DIS_KEEP_TARGET_S:
            self.discharge.reset()  # na laden of een lange pauze opnieuw vanaf 0 W
        if mode not in ("charge", "solar", "dis", "dis_sell", "dis_hold"):
            # Alleen laden aansturen: na het laden één keer terug naar de ontlaadstand,
            # daarna niets meer doen (andere automatiseringen regelen het ontladen).
            if self.target == "released":
                return
            mode = "released"
        option, power = self._target(mode)
        async with self._lock:
            done: list[str] = []
            try:
                if mode_entity and option and await self._set_option(mode_entity, option):
                    done.append(f"{mode_entity} → {option}")
                    if power_entity:
                        await asyncio.sleep(MODE_SETTLE_S)
                if power_entity and power is not None:
                    min_change = MIN_CHANGE_W if mode == "solar" else DIS_MIN_CHANGE_W if mode == "dis" else 0
                    written = await self._set_power(power_entity, power, min_change)
                    if written is not None:
                        done.append(f"{power_entity} → {written:g} W")
            except Exception as err:  # noqa: BLE001 - nooit de integratie laten crashen
                self._error(f"{type(err).__name__}: {err}")
                return
        self.target = mode
        self.last_error = None
        if done:
            self.last_command = f"{mode} ({reason}): " + ", ".join(done)
            self.last_command_at = dt_util.utcnow()
            _LOGGER.info("Battery plan: %s", self.last_command)
        self._notify()

    def _error(self, text: str) -> None:
        if text != self.last_error:
            _LOGGER.warning("Execute battery plan: %s", text)
        self.last_error = text
        self._notify()

    async def _set_option(self, entity_id: str, option: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            raise RuntimeError(f"{entity_id} is not available")
        if state.state == option:
            return False
        options = state.attributes.get("options")
        if options and option not in options:
            raise ValueError(f"'{option}' is not an option of {entity_id} ({', '.join(map(str, options))})")
        domain = entity_id.split(".", 1)[0]
        await self.hass.services.async_call(
            domain, "select_option", {"entity_id": entity_id, "option": option}, blocking=True
        )
        return True

    async def _set_power(self, entity_id: str, power: float, min_change: float = 0) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            raise RuntimeError(f"{entity_id} is not available")
        attrs = state.attributes
        lo, hi = attrs.get("min"), attrs.get("max")
        if lo is not None:
            power = max(float(lo), power)
        if hi is not None:
            power = min(float(hi), power)
        step = float(attrs.get("step") or 1)
        power = round(power / step) * step
        if state.state != STATE_UNKNOWN:
            try:
                current = float(state.state)
                diff = abs(current - power)
                if diff < max(step / 2, 1) or (diff < min_change and power != 0):
                    return None
            except ValueError:
                pass
        domain = entity_id.split(".", 1)[0]
        await self.hass.services.async_call(
            domain, "set_value", {"entity_id": entity_id, "value": power}, blocking=True
        )
        return power
