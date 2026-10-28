"""DataUpdateCoordinator voor Accu Planner."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DIS_ENABLED,
    CONF_DIS_SELL_ENABLED,
    CONF_DIS_SELL_POWER,
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
    STORAGE_VERSION,
)
from .planner import (
    CostState,
    SavingsLedger,
    Settings,
    StepSeries,
    charge_unit_cost,
    parse_profile,
    plan,
    price_value,
    q_start,
    read_prices,
    reconstruct_cost,
)

_LOGGER = logging.getLogger(__name__)

COST_HISTORY_DAYS = 7
STORE_FORMAT = 2  # opgeslagen kostprijzen van oudere versies worden genegeerd
HISTORY_KEEP_S = 26 * 3600  # prijzen en kwartierlog zo lang bewaren (grafiek toont max 24 uur terug)

type AccuPlannerConfigEntry = ConfigEntry["AccuPlannerCoordinator"]


class AccuPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Berekent periodiek (en bij SOC-/prijswijziging) de planning."""

    config_entry: AccuPlannerConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AccuPlannerConfigEntry) -> None:
        self.conf: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=float(self.conf[CONF_UPDATE_INTERVAL])),
        )
        self.cost = CostState()
        self.ledger = SavingsLedger()
        self.price_cache: dict[int, float] = {}  # eerdere kwartierprijzen (de sensor toont alleen vandaag/morgen)
        self.quarter_log: dict[int, dict[str, Any]] = {}  # per verstreken kwartier: plan, gemeten SOC, drempel
        self._reconstructed = False
        self.executor = None  # PlanExecutor, gezet in __init__
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    async def async_load(self) -> None:
        """Laad de bewaarde kostprijs (alleen in het huidige formaat) en de besparing per dag."""
        data = await self._store.async_load() or {}
        if data.get("format") == STORE_FORMAT:
            self.cost = CostState(
                avg=data.get("avg"), kwh=data.get("kwh"), t=data.get("t"), source=data.get("source") or "live"
            )
        self.price_cache = {int(k): float(v) for k, v in (data.get("price_cache") or {}).items()}
        self.quarter_log = {int(k): dict(v) for k, v in (data.get("quarter_log") or {}).items()}
        ledger = data.get("ledger") or {}
        self.ledger = SavingsLedger(
            days={k: list(v) for k, v in (ledger.get("days") or {}).items()}, total=float(ledger.get("total") or 0)
        )

    @callback
    def async_start_listeners(self) -> None:
        """Herbereken direct als SOC of prijzen veranderen (debounced)."""
        entities = [self.conf[CONF_SOC_SENSOR], self.conf[CONF_PRICE_SENSOR]]
        if self.conf.get(CONF_SELL_PRICE_SENSOR):
            entities.append(self.conf[CONF_SELL_PRICE_SENSOR])

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        self.config_entry.async_on_unload(async_track_state_change_event(self.hass, entities, _changed))

        # bij elk nieuw kwartier direct opnieuw plannen (de uitvoering volgt dan meteen)
        @callback
        def _quarter(_now) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        self.config_entry.async_on_unload(
            async_track_time_change(self.hass, _quarter, minute=[0, 15, 30, 45], second=3)
        )

    async def async_set_cost_price(self, cost_ct: float) -> None:
        """Zet de kostprijs van de accu-inhoud handmatig (cent per kWh)."""
        self.cost.avg = cost_ct / 100
        self.cost.source = "manual"
        self._save()
        await self.async_request_refresh()

    def _store_data(self) -> dict[str, Any]:
        return {
            "format": STORE_FORMAT,
            **asdict(self.cost),
            "ledger": {"days": self.ledger.days, "total": self.ledger.total},
            "price_cache": {str(k): v for k, v in self.price_cache.items()},
            "quarter_log": {str(k): v for k, v in self.quarter_log.items()},
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._store_data, 30)

    async def async_flush(self) -> None:
        """Direct opslaan (bij herladen/verwijderen), zodat er niets verloren gaat."""
        await self._store.async_save(self._store_data())

    # ------------------------------------------------------------ kostprijs
    def _pv_watts(self, value: float, unit: str | None) -> float:
        return value * 1000 if (unit or "").lower() == "kw" else value

    def _unit_cost_fn(self, price_at, pv_at):
        """Functie (delta_kwh, uren, tijdstip) -> kostprijs per kWh in de accu."""
        eff = float(self.conf[CONF_EFFICIENCY]) / 100
        solar = float(self.conf[CONF_SOLAR_COST]) / 100

        def fn(delta: float, hours: float, ts: float) -> float | None:
            return charge_unit_cost(delta, hours, price_at(ts), eff, pv_at(ts) if pv_at else None, solar)

        return fn

    async def _async_reconstruct_cost(self, capacity: float, prices: dict[int, float]) -> None:
        """Kostprijs berekenen uit de recorder-historie (SOC, inkoopprijs, optioneel PV)."""
        if "recorder" not in self.hass.config.components:
            _LOGGER.info("Recorder not active; stored energy cost is tracked live from now on")
            return
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        c = self.conf
        soc_id, price_id, pv_id = c[CONF_SOC_SENSOR], c[CONF_PRICE_SENSOR], c.get(CONF_PV_POWER_SENSOR) or None
        ids = [i for i in (soc_id, price_id, pv_id) if i]
        start = dt_util.utcnow() - timedelta(days=COST_HISTORY_DAYS)
        hist = await get_instance(self.hass).async_add_executor_job(
            partial(
                history.get_significant_states,
                self.hass,
                start,
                None,
                ids,
                significant_changes_only=False,
                no_attributes=False,
            )
        )

        def points(entity_id: str, conv=float) -> list[tuple[float, float]]:
            out = []
            for st in hist.get(entity_id, []):
                try:
                    out.append((st.last_changed.timestamp(), conv(float(str(st.state).replace(",", ".")))))
                except (ValueError, AttributeError):
                    continue
            return out

        soc_pts = points(soc_id)
        if len(soc_pts) < 2:
            _LOGGER.info("Not enough SOC history to calculate the stored energy cost")
            return
        price_series = StepSeries(points(price_id, price_value))

        def price_at(ts: float) -> float | None:
            p = prices.get(q_start(ts))  # kwartierprijs uit de attributen (vandaag/morgen)
            return p if p is not None else price_series.at(ts)

        pv_at = None
        if pv_id:
            unit = None
            if cur := self.hass.states.get(pv_id):
                unit = cur.attributes.get("unit_of_measurement")
            pv_series = StepSeries([(t, self._pv_watts(v, unit)) for t, v in points(pv_id)])
            pv_at = pv_series.at

        fill_ledger = not self.ledger.days
        ledger = SavingsLedger()
        tz = dt_util.get_default_time_zone()
        result = reconstruct_cost(
            soc_pts, capacity, self._unit_cost_fn(price_at, pv_at), value_fn=price_at, ledger=ledger, tz=tz
        )
        if self.cost.avg is None and result.avg is not None:
            self.cost = result
            _LOGGER.info(
                "Stored energy cost calculated from %d days of history: %.4f EUR/kWh", COST_HISTORY_DAYS, result.avg
            )
        if fill_ledger and ledger.days:
            self.ledger = ledger
            # live verder vanaf de laatste historische stand (niets dubbel tellen)
            self.cost.kwh, self.cost.t = result.kwh, result.t
            _LOGGER.info("Savings of %d days filled in from history: EUR %.2f", len(ledger.days), ledger.total)
        self._save()

    def _track_cost(self, e0: float, prices: dict[int, float], now_ts: float) -> None:
        """Verwerk de SOC-verandering sinds de vorige run."""
        pv_at = None
        if pv_id := self.conf.get(CONF_PV_POWER_SENSOR):
            st = self.hass.states.get(pv_id)
            try:
                pv_w = self._pv_watts(float(st.state), st.attributes.get("unit_of_measurement")) if st else None
            except ValueError:
                pv_w = None
            if pv_w is not None:
                pv_at = lambda ts: pv_w  # noqa: E731
        before = self.cost.avg
        if before is None and self.cost.kwh and self.cost.kwh > 0.05 and e0 - self.cost.kwh > 0.01 and prices:
            # onbekende bestaande inhoud: laten meewegen tegen het goedkoopste terugladen
            self.cost.avg = min(prices.values()) / (float(self.conf[CONF_EFFICIENCY]) / 100)
        saved = self.cost.step(
            e0, now_ts, self._unit_cost_fn(lambda ts: prices.get(q_start(ts)), pv_at), lambda ts: prices.get(q_start(ts))
        )
        if saved:
            self.ledger.add(now_ts, saved[0], saved[1], dt_util.get_default_time_zone())
        if self.cost.avg != before and self.cost.source == "none":
            self.cost.source = "live"

    def _log_quarter(self, result: dict[str, Any], soc: float, now_ts: float, cutoff: float) -> None:
        """Onthoud per kwartier wat er gepland was, de SOC aan het begin en de drempel."""
        q = q_start(now_ts)
        series = result["series"]
        i = series["now"]
        entry = self.quarter_log.setdefault(q, {"s": round(soc, 1)})  # SOC bij de eerste meting
        act = series["plan"][i] if i < len(series["plan"]) else "-"
        if act != "-" or "a" not in entry:
            entry["a"] = act  # een actie in dit kwartier blijft staan
        entry["d"] = series["threshold"][i]
        self.quarter_log = {t: v for t, v in self.quarter_log.items() if t >= cutoff}
        # verleden in de grafiekreeks invullen met wat er toen gepland was en de gemeten SOC
        start = datetime.fromisoformat(series["start"]).timestamp()
        for k in range(i):
            log = self.quarter_log.get(int(start) + k * 900)
            if log:
                series["plan"] = series["plan"][:k] + (log.get("a") or ".") + series["plan"][k + 1 :]
                series["soc"][k] = log.get("s")
                series["threshold"][k] = log.get("d")

    def _number(self, entity_id: str) -> tuple[float, str | None]:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            raise UpdateFailed(f"{entity_id} is not available")
        try:
            return float(str(state.state).replace(",", ".")), state.attributes.get("unit_of_measurement")
        except ValueError as err:
            raise UpdateFailed(f"{entity_id} has no numeric value: {state.state}") from err

    async def _async_update_data(self) -> dict[str, Any]:
        c = self.conf
        tz = dt_util.get_default_time_zone()

        capacity, unit = self._number(c[CONF_CAPACITY_SENSOR])
        if (unit or "").lower() == "wh" or capacity > 200:
            capacity /= 1000
        if capacity <= 0:
            raise UpdateFailed("capacity is 0")
        soc, _ = self._number(c[CONF_SOC_SENSOR])

        price_state = self.hass.states.get(c[CONF_PRICE_SENSOR])
        if price_state is None:
            raise UpdateFailed(f"{c[CONF_PRICE_SENSOR]} not found")
        prices = read_prices(dict(price_state.attributes), tz)
        if not prices:
            raise UpdateFailed(f"no price data in the attributes of {c[CONF_PRICE_SENSOR]}")
        # verstreken prijzen onthouden, zodat de grafiek tot 24 uur terug kan tonen
        cutoff = dt_util.utcnow().timestamp() - HISTORY_KEEP_S
        self.price_cache = {t: p for t, p in {**self.price_cache, **prices}.items() if t >= cutoff}
        prices = {**self.price_cache, **prices}

        sell_prices = None
        if sell_id := c.get(CONF_SELL_PRICE_SENSOR):
            sell_state = self.hass.states.get(sell_id)
            sell_prices = read_prices(dict(sell_state.attributes), tz) if sell_state else {}
            if not sell_prices:
                _LOGGER.warning("No sell prices in %s; the purchase price is used", sell_id)

        profile, errors = parse_profile(c[CONF_PROFILE])
        if errors:
            _LOGGER.warning("Profile blocks not understood: %s", errors)
        weekend = parse_profile(c[CONF_PROFILE_WEEKEND])[0] if c.get(CONF_PROFILE_WEEKEND) else None

        settings = Settings(
            efficiency=float(c[CONF_EFFICIENCY]) / 100,
            wear_ct=float(c[CONF_WEAR_COST]),
            charge_w=float(c[CONF_CHARGE_POWER]),
            discharge_w=float(c[CONF_DISCHARGE_POWER]),
            min_soc=float(c[CONF_MIN_SOC]),
            max_soc=float(c[CONF_MAX_SOC]),
            cost_floor=bool(c[CONF_COST_FLOOR]),
            # export is planned only when discharge control will actually sell
            allow_export=bool(c[CONF_DIS_ENABLED] and c[CONF_DIS_SELL_ENABLED]),
            profit_pct=float(c[CONF_PROFIT_PCT]),
            sell_w=float(c[CONF_DIS_SELL_POWER]),
        )

        e0 = capacity * min(100.0, max(0.0, soc)) / 100
        now_ts = dt_util.utcnow().timestamp()
        if (self.cost.avg is None or not self.ledger.days) and not self._reconstructed:
            self._reconstructed = True
            try:
                await self._async_reconstruct_cost(capacity, prices)
            except Exception:  # noqa: BLE001 - historie is een extraatje, nooit fataal
                _LOGGER.exception("Calculating stored energy cost from history failed")
        self._track_cost(e0, prices, now_ts)

        try:
            result = await self.hass.async_add_executor_job(
                plan, dt_util.utcnow().timestamp(), prices, capacity, soc, profile, weekend, tz, settings, self.cost.avg,
                sell_prices or None,
            )
        except ValueError as err:
            raise UpdateFailed(str(err)) from err

        self._save()

        result["avg_cost"] = round(self.cost.avg, 4) if self.cost.avg is not None else result["cost"]
        result["avg_cost_estimated"] = self.cost.avg is None
        result["avg_cost_source"] = self.cost.source if self.cost.avg is not None else "estimate"
        result["savings"] = sav = self.ledger.summary(now_ts, tz)
        result["savings_total"] = sav["total"]
        row = {
            "icon": "💶", "kind": "saved",
            "title": f"Saved: today €{sav['today']['eur']:.2f} · yesterday €{sav['yesterday']['eur']:.2f}",
            "sub": f"this week €{sav['this_week']['eur']:.2f} · last week €{sav['previous_week']['eur']:.2f} · "
                   f"this month €{sav['this_month']['eur']:.2f} · last month €{sav['previous_month']['eur']:.2f}",
        }
        idx = next((i for i, r in enumerate(result["report"]) if r.get("kind") == "savings"), len(result["report"]))
        result["report"].insert(idx + 1, row)
        result["text"] += f"\n{row['icon']} {row['title']}\n({row['sub']})"
        self._log_quarter(result, soc, now_ts, cutoff)
        result["capacity_kwh"] = capacity
        result["soc"] = soc
        return result
