"""Planningslogica voor de accu (geen Home Assistant-imports, los te testen).

Werkwijze: dynamische programmering over de accu-inhoud (stappen van 0,05 kWh).
Per kwartier kiest de planner laden, niets of ontladen zo dat de totale
energierekening tot het einde van de bekende prijzen minimaal is:
  - laden kost inkoopprijs / rendement per kWh in de accu;
  - ontladen voor eigen verbruik bespaart de inkoopprijs, terugleveren levert de
    verkoopprijs op (alleen als er een verkoopsensor is); elke kWh uit de accu
    kost afschrijving + ontlaadmarge;
  - energie die aan het eind nog in de accu zit, is waard wat het zou kosten om
    haar in de goedkoopste kwartieren opnieuw te laden.
Doordat de hele horizon in één keer wordt bekeken, laadt de accu overdag alleen
wat nodig is als 's nachts goedkoper geladen kan worden.

De ontlaadprijs is de waarde van 1 kWh in de accu (wat die later nog bespaart of
wat terugladen kost) + afschrijving + marge: daarboven wordt ontladen.
"""
from __future__ import annotations

import bisect
import re
from collections.abc import Callable

import numpy as np
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

Q = 900  # seconden per kwartier
EPS = 1e-6
HISTORY_QUARTERS = 96  # 24 uur historie in de grafiekreeks
STEP = 0.05  # kWh per rekenstap


def q_start(ts: float) -> int:
    """Begin van het kwartier (epoch seconden)."""
    return int(ts // Q * Q)


# ---------------------------------------------------------------- profiel
@dataclass
class Profile:
    """Verbruiksprofiel: blokken (van uur, tot uur, kW) en een restwaarde."""

    blocks: list[tuple[float, float, float]] = field(default_factory=list)
    rest: float = 0.0


_BLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*=\s*([\d.,]+)$")
_REST = re.compile(r"^(\*|rest)\s*=\s*([\d.,]+)$", re.IGNORECASE)


def parse_profile(text: str | None) -> tuple[Profile, list[str]]:
    """Parse '07-10=1.5; 17:30-19=2.5; 22-07=1.0; *=0.4'. Geeft (profiel, fouten)."""
    prof = Profile()
    errors: list[str] = []
    for part in re.split(r"[;\n|]", text or ""):
        seg = part.strip()
        if not seg:
            continue
        try:
            if m := _REST.match(seg):
                prof.rest = float(m[2].replace(",", "."))
                continue
            if m := _BLOCK.match(seg):
                start = int(m[1]) + int(m[2] or 0) / 60
                end = int(m[3]) + int(m[4] or 0) / 60
                if start > 24 or end > 24:
                    raise ValueError
                prof.blocks.append((start, end, float(m[5].replace(",", "."))))
                continue
        except ValueError:
            pass
        errors.append(seg)
    return prof, errors


def profile_kw(prof: Profile, local: datetime) -> float:
    """Verwacht verbruik (kW) op een lokaal tijdstip."""
    h = local.hour + local.minute / 60
    for start, end, kw in prof.blocks:
        hit = (start <= h < end) if start < end else (h >= start or h < end)
        if hit:
            return kw
    return prof.rest


# ---------------------------------------------------------------- prijzen
_START_KEYS = ("start_date", "start", "from", "datetime", "time", "startsAt")
_END_KEYS = ("end_date", "end", "till", "endsAt")
_PRICE_KEYS = ("price", "value", "total", "electricity_price", "price_eur")


def _ts(value: Any, tz: tzinfo) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


def _eur(entry: dict) -> float | None:
    pti = entry.get("price_tax_included")
    if isinstance(pti, dict) and pti.get("amount") is not None:
        try:
            return float(pti["amount"]) / 1e7  # Zonneplan
        except (TypeError, ValueError):
            return None
    for key in _PRICE_KEYS:
        if entry.get(key) is not None:
            try:
                val = float(entry[key])
            except (TypeError, ValueError):
                return None
            break
    else:
        return None
    if abs(val) > 1000:
        return val / 1e7  # Zonneplan-eenheden
    if abs(val) > 5:
        return val / 100  # cent/kWh
    return val  # EUR/kWh


def read_prices(attributes: dict, tz: tzinfo) -> dict[int, float]:
    """Kwartierprijzen (EUR/kWh) uit de attributen van een prijssensor.

    Ondersteunt Zonneplan (forecast) en generieke lijsten met start/end +
    price/value (Nordpool raw_today/raw_tomorrow, EnergyZero, Tibber, ...).
    Uurprijzen worden in kwartieren gesplitst.
    """
    out: dict[int, float] = {}
    for val in attributes.values():
        if not isinstance(val, (list, tuple)):
            continue
        for entry in val:
            if not isinstance(entry, dict):
                continue
            start = _ts(next((entry[k] for k in _START_KEYS if entry.get(k) is not None), None), tz)
            price = _eur(entry)
            if start is None or price is None:
                continue
            end = _ts(next((entry[k] for k in _END_KEYS if entry.get(k) is not None), None), tz)
            if end is None or end <= start:
                end = start + Q
            t = q_start(start)
            while t < end:
                out[t] = price
                t += Q
    return out


# ---------------------------------------------------------------- kostprijs
@dataclass
class CostState:
    """Gewogen gemiddelde kostprijs (EUR per kWh IN de accu).

    Bij elke SOC-stijging wordt de energie toegevoegd tegen de kostprijs van dat
    moment; ontladen verandert het gemiddelde niet.
    """

    avg: float | None = None
    kwh: float | None = None
    t: float | None = None  # tijdstip van de laatst verwerkte stand
    source: str = "none"  # history / live / manual

    def step(
        self,
        e: float,
        ts: float,
        unit_cost_fn: Callable[[float, float, float], float | None],
        value_fn: Callable[[float], float | None] | None = None,
    ) -> tuple[float, float] | None:
        """Verwerk een nieuwe accu-inhoud e (kWh) op tijdstip ts.

        unit_cost_fn(delta_kwh, uren, midden_tijdstip) geeft de kostprijs per kWh in
        de accu voor een stijging (None = onbekend, niet meetellen).
        value_fn(tijdstip) geeft wat een kWh uit de accu op dat moment waard is
        (de inkoopprijs). Bij ontladen geeft step (besparing EUR, kWh) terug:
        kWh x (waarde - kostprijs).
        """
        result = None
        if self.kwh is not None and self.t is not None and ts > self.t:
            delta = e - self.kwh
            mid = (ts + self.t) / 2
            if delta > 0.01:
                unit = unit_cost_fn(delta, (ts - self.t) / 3600, mid)
                if unit is not None:
                    if self.avg is None or self.kwh < 0.05:
                        self.avg = unit
                    else:
                        self.avg = (self.avg * self.kwh + unit * delta) / (self.kwh + delta)
            elif delta < -0.01 and value_fn is not None and self.avg is not None:
                value = value_fn(mid)
                if value is not None:
                    result = (-delta * (value - self.avg), -delta)
        self.kwh = e
        self.t = ts
        return result


@dataclass
class SavingsLedger:
    """Werkelijke besparing per kalenderdag: {"2026-10-26": [eur, kwh_uit]}."""

    days: dict[str, list[float]] = field(default_factory=dict)
    total: float = 0.0
    MAX_DAYS = 400

    def add(self, ts: float, eur: float, kwh: float, tz: tzinfo) -> None:
        day = datetime.fromtimestamp(ts, tz).date().isoformat()
        cur = self.days.setdefault(day, [0.0, 0.0])
        cur[0] += eur
        cur[1] += kwh
        self.total += eur
        if len(self.days) > self.MAX_DAYS:
            for old in sorted(self.days)[: len(self.days) - self.MAX_DAYS]:
                del self.days[old]

    def _sum(self, first: date, last: date) -> tuple[float, float, int]:
        eur = kwh = 0.0
        n = 0
        for day, (e, k) in self.days.items():
            d = date.fromisoformat(day)
            if first <= d <= last:
                eur += e
                kwh += k
                n += 1
        return round(eur, 2), round(kwh, 2), n

    def summary(self, now: float, tz: tzinfo) -> dict[str, Any]:
        today = datetime.fromtimestamp(now, tz).date()
        yday = today - timedelta(days=1)
        week_start = today - timedelta(days=today.weekday() + 7)  # maandag vorige week
        month_last = today.replace(day=1) - timedelta(days=1)
        out = {}
        for key, first, last in (
            ("today", today, today),
            ("this_week", today - timedelta(days=today.weekday()), today),
            ("this_month", today.replace(day=1), today),
            ("yesterday", yday, yday),
            ("last_7_days", today - timedelta(days=7), yday),
            ("last_30_days", today - timedelta(days=30), yday),
            ("previous_week", week_start, week_start + timedelta(days=6)),
            ("previous_month", month_last.replace(day=1), month_last),
        ):
            eur, kwh, n = self._sum(first, last)
            out[key] = {"eur": eur, "kwh": kwh, "days_with_data": n, "from": first.isoformat(), "to": last.isoformat()}
        out["total"] = round(self.total, 2)
        out["first_day"] = min(self.days) if self.days else None
        return out


def charge_unit_cost(
    delta_kwh: float, hours: float, price: float | None, eff: float, pv_w: float | None, solar_cost: float
) -> float | None:
    """Kostprijs per kWh in de accu voor een SOC-stijging.

    Zonder PV-meting telt alles als netstroom (inkoopprijs / rendement). Met PV-meting
    telt het deel van het laadvermogen dat de PV levert als zonne-energie.
    """
    if price is None:
        return None
    grid = price / eff
    if pv_w is None:
        return grid
    rate_w = delta_kwh / max(hours, 1 / 60) * 1000
    share = min(1.0, max(0.0, pv_w) / rate_w) if rate_w > 0 else 1.0
    return share * solar_cost + (1 - share) * grid


class StepSeries:
    """Tijdreeks met 'laatste waarde op of voor t'-opzoeking."""

    def __init__(self, points: list[tuple[float, float]]) -> None:
        points = sorted(points)
        self._t = [p[0] for p in points]
        self._v = [p[1] for p in points]

    def at(self, ts: float) -> float | None:
        i = bisect.bisect_right(self._t, ts) - 1
        return self._v[i] if i >= 0 else None


def price_value(raw: float) -> float:
    """Zet een prijs om naar EUR/kWh (Zonneplan-eenheden, centen of euro)."""
    if abs(raw) > 1000:
        return raw / 1e7
    if abs(raw) > 5:
        return raw / 100
    return raw


def reconstruct_cost(
    soc_points: list[tuple[float, float]],
    capacity: float,
    unit_cost_fn: Callable[[float, float, float], float | None],
    value_fn: Callable[[float], float | None] | None = None,
    ledger: SavingsLedger | None = None,
    tz: tzinfo | None = None,
) -> CostState:
    """Bereken kostprijs (en optioneel de besparing per dag) uit het SOC-verloop."""
    st = CostState()
    for ts, soc in sorted(soc_points):
        saved = st.step(capacity * min(100.0, max(0.0, soc)) / 100, ts, unit_cost_fn, value_fn)
        if saved and ledger is not None and tz is not None:
            ledger.add(ts, saved[0], saved[1], tz)
    st.source = "history" if st.avg is not None else "none"
    return st


# ---------------------------------------------------------------- planning
@dataclass
class Settings:
    """Instellingen voor de planning."""

    efficiency: float = 0.85
    wear_ct: float = 5.0  # slijtage & ontlaadmarge per kWh uit de accu
    charge_w: float = 3300
    discharge_w: float = 3500
    min_soc: float = 5
    max_soc: float = 100
    cost_floor: bool = False
    allow_export: bool = False
    profit_pct: float = 20.0
    sell_w: float = 3500  # power when selling at full power (Battery discharge settings)


@dataclass
class Slot:
    start: int
    price: float
    sell: float | None  # feed-in price offered to the optimiser (None = no export in the optimisation)
    hours: float
    max_in: float  # kWh added to the battery
    load: float  # kWh consumption
    quote: float | None = None  # feed-in price of this quarter (sell sensor, else purchase price)


_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def plan(
    now: float,
    prices: dict[int, float],
    capacity: float,
    soc: float,
    profile: Profile,
    profile_weekend: Profile | None,
    tz: tzinfo,
    s: Settings,
    avg_cost: float | None,
    sell_prices: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Maak de planning. Geeft een dict met resultaten."""
    eff = min(1.0, max(0.5, s.efficiency))
    extra = s.wear_ct / 100
    e0 = capacity * min(100.0, max(0.0, soc)) / 100
    emin = min(capacity * s.min_soc / 100, e0)
    emax = max(capacity * s.max_soc / 100, e0)

    def local(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, tz)

    def load_kw(ts: int) -> float:
        lt = local(ts)
        prof = profile_weekend if profile_weekend and lt.weekday() >= 5 else profile
        return profile_kw(prof, lt)

    # aaneengesloten kwartieren vanaf nu (stopt bij het eerste gat)
    slots: list[Slot] = []
    t = q_start(now)
    while t in prices:
        hours = (t + Q - max(now, t)) / 3600
        slots.append(
            Slot(
                start=t,
                price=prices[t],
                # export is not optimised freely: it follows the discharge rule below (like the execution)
                sell=None,
                quote=sell_prices.get(t, prices[t]) if sell_prices else prices[t],
                hours=hours,
                max_in=s.charge_w / 1000 * hours * eff,
                load=min(load_kw(t), s.discharge_w / 1000) * hours,
            )
        )
        t += Q
    n = len(slots)
    if n == 0:
        raise ValueError("no price for the current quarter")

    cost = avg_cost if avg_cost is not None else min(sl.price / eff for sl in slots)
    floor = cost + extra if s.cost_floor else -np.inf
    # verkoopprijs: (kostprijs accu-inhoud + slijtage) x (1 + winst%); terugleveren alleen daarboven.
    # De kostprijs is per kWh IN de accu en bevat het laadrendement al (inkoopprijs / rendement).
    sell_base = cost + s.wear_ct / 100
    sell_price = sell_base * (1 + s.profit_pct / 100)

    # ---- rekenrooster
    kmax = max(1, round(emax / STEP))
    kmin = min(int(np.ceil(emin / STEP - 1e-9)), kmax)
    k0 = min(kmax, max(0, round(e0 / STEP)))
    ks_all = np.arange(kmax + 1)

    # eindwaarde: k-de kWh van boven = goedkoopste terugladen (afnemende waarde naar boven)
    refill = sorted((sl.price / eff, sl.max_in) for sl in slots)
    unit_val = np.empty(kmax + 1)
    cum, idx = 0.0, 0
    for k in range(kmax, 0, -1):  # van vol naar leeg
        pos = (kmax - k + 0.5) * STEP
        while idx < len(refill) - 1 and pos >= cum + refill[idx][1]:
            cum += refill[idx][1]
            idx += 1
        unit_val[k] = refill[idx][0]
    unit_val[0] = 0.0
    V = -np.cumsum(unit_val) * STEP  # V[k] = -(waarde in EUR van k stappen inhoud)

    Vs = [None] * (n + 1)
    Vs[n] = V
    policy = [None] * n
    INF = 1e18
    for i in range(n - 1, -1, -1):
        sl = slots[i]
        max_up = int(sl.max_in / STEP + 1e-9)
        max_out = s.discharge_w / 1000 * sl.hours
        load_steps = int(min(sl.load, max_out) / STEP + 1e-9)
        if sl.load > EPS:
            load_steps = max(1, load_steps)
        self_ok = sl.price >= floor
        sell_ok = sl.sell is not None and sl.sell >= floor
        out_steps = int(max_out / STEP + 1e-9) if sell_ok else (load_steps if self_ok else 0)
        # opbrengst per ontlaadstap: eerst eigen verbruik (inkoopprijs), daarna terugleveren
        gains = []
        for u in range(out_steps):
            if u < load_steps and self_ok:
                gains.append(sl.price - extra)
            elif sell_ok:
                gains.append(sl.sell - extra)
            else:
                break
        gain_cum = np.concatenate(([0.0], np.cumsum(gains) * STEP))
        charge_step = STEP / eff * sl.price + 1e-7  # klein extraatje: bij gelijke kosten niet laden

        best = V.copy()
        pol = np.zeros(kmax + 1, dtype=np.int16)
        for d in range(1, max_up + 1):
            if d > kmax:
                break
            cand = np.full(kmax + 1, INF)
            cand[: kmax + 1 - d] = d * charge_step + V[d:]
            better = cand < best - 1e-12
            best[better] = cand[better]
            pol[better] = d
        for d in range(1, len(gain_cum)):
            if kmin + d > kmax:
                break
            cand = np.full(kmax + 1, INF)
            cand[kmin + d:] = -gain_cum[d] + V[kmin: kmax + 1 - d]
            better = cand < best - 1e-12
            best[better] = cand[better]
            pol[better] = -d
        V = best
        Vs[i] = V
        policy[i] = pol

    # ---- pad volgen
    path = [k0]
    k = k0
    for i in range(n):
        k = int(k + policy[i][k])
        path.append(k)
    c_in = [0.0] * n  # kWh erbij in de accu
    d_out = [0.0] * n  # kWh uit de accu
    export = [0.0] * n  # waarvan teruggeleverd
    for i in range(n):
        d = path[i + 1] - path[i]
        if d > 0:
            c_in[i] = d * STEP
        elif d < 0:
            d_out[i] = -d * STEP
            export[i] = max(0.0, d_out[i] - slots[i].load) if slots[i].sell is not None else 0.0
    # ---- selling, exactly like the execution (Battery discharge settings): in a quarter where the
    # plan discharges (price above the discharge price) and the feed-in price is above the sell
    # price, discharge at full power; later discharges get what is left above the minimum SOC
    if s.allow_export:
        lvl = e0
        for i, sl in enumerate(slots):
            lvl = min(emax, lvl + c_in[i])
            avail = max(0.0, lvl - emin)
            if d_out[i] > 0 and sl.quote is not None and sl.quote > sell_price:
                take = min(max(d_out[i], s.sell_w / 1000 * sl.hours), avail)
                export[i] = max(0.0, take - sl.load)
                d_out[i] = take
            else:
                d_out[i] = min(d_out[i], avail)
            lvl -= d_out[i]
    level = [e0] + [0.0] * n
    for i in range(n):
        level[i + 1] = level[i] + c_in[i] - d_out[i]
    level = [min(emax, max(0.0, lv)) for lv in level]

    # ---- ontlaaddrempel: waarde van de volgende kWh op het geplande pad + afschrijving + marge
    thresholds: list[float] = []
    for i in range(n):
        kk = path[i + 1] + 1 if path[i + 1] < path[i] else path[i + 1]
        kk = max(kmin + 1, min(kmax, kk))
        mv = float(Vs[i + 1][kk - 1] - Vs[i + 1][kk]) / STEP
        thresholds.append(max(mv + extra, floor))

    # ---- nu
    cur = slots[0]
    if c_in[0] > 0.001:
        mode = "charge"
    elif d_out[0] > 0.001:
        mode = "discharge"
    else:
        mode = "idle"
    charge_w = round(min(s.charge_w, c_in[0] / eff / cur.hours * 1000)) if mode == "charge" else 0
    discharge_w = round(min(s.discharge_w, d_out[0] / cur.hours * 1000)) if mode == "discharge" else 0

    # verstreken kwartieren (alleen voor de grafiek)
    past: list[int] = []
    t = q_start(now) - Q
    while t in prices and len(past) < HISTORY_QUARTERS:
        past.append(t)
        t -= Q
    past.reverse()

    # ---- samenvatting
    today = local(now).date()

    def fmt(ts: float) -> str:
        return local(ts).strftime("%H:%M")

    def fmt_at(ts: float) -> str:
        lt = local(ts)
        return ("" if lt.date() == today else _DAYS[lt.weekday()] + " ") + fmt(ts)

    def act(i: int) -> str:
        if c_in[i] > 0.001:
            return "C"
        if export[i] > 0.001:
            return "S"
        if d_out[i] > 0.001:
            return "D"
        return "-"

    acts = [act(i) for i in range(n)]

    def ranges(codes: str) -> list[tuple[int, int]]:
        out: list[list[int]] = []
        for i, sl in enumerate(slots):
            if acts[i] not in codes:
                continue
            if out and out[-1][1] == sl.start:
                out[-1][1] = sl.start + Q
            else:
                out.append([sl.start, sl.start + Q])
        return [(a, b) for a, b in out]

    def ranges_where(pred) -> list[tuple[int, int]]:
        out: list[list[int]] = []
        for i, sl in enumerate(slots):
            if not pred(i):
                continue
            if out and out[-1][1] == sl.start:
                out[-1][1] = sl.start + Q
            else:
                out.append([sl.start, sl.start + Q])
        return [(a, b) for a, b in out]

    def fmt_range(r: tuple[int, int]) -> str:
        return f"{fmt_at(r[0])}-{fmt(r[1])}"

    def slot_ts(i: int) -> float:
        return max(now, slots[i].start) if i < n else slots[-1].start + Q

    charge_r, discharge_r, sell_r = ranges("C"), ranges("DS"), ranges("S")
    charge_q = sum(1 for a in acts if a == "C")
    grid_in = sum(c / eff for c in c_in)
    grid_cost = sum(c / eff * sl.price for c, sl in zip(c_in, slots))
    charge_hours = sum(sl.hours for a, sl in zip(acts, slots) if a == "C")
    avg_charge_w = round(grid_in / charge_hours * 1000) if charge_hours > EPS else 0
    out_kwh = sum(d_out)
    export_kwh = sum(export)
    self_kwh = out_kwh - export_kwh
    avoided = sum((d - e) * sl.price for d, e, sl in zip(d_out, export, slots))
    export_rev = sum(e * (sl.quote or 0) for e, sl in zip(export, slots))
    revenue = avoided + export_rev
    load_total = sum(sl.load for sl in slots)
    # verwachte besparing tot het einde van de horizon: opbrengst (vermeden inkoop +
    # teruglevering) - laadkosten, gecorrigeerd voor meer/minder inhoud aan het eind
    stock_delta = level[n] - e0
    expected_savings = revenue - grid_cost + stock_delta * cost
    expected_wear = out_kwh * s.wear_ct / 100
    soc_fc = [round(lv / capacity * 1000) / 10 for lv in level]
    soc_min_i = min(range(n + 1), key=lambda i: level[i])
    end_ts = slots[-1].start + Q
    discharge_price = round(thresholds[0], 4)
    value_now = thresholds[0] - extra

    cycles, prev = 0, ""
    for a in acts:
        m = "c" if a == "C" else "d" if a in "DS" else ""
        if m and m != prev:
            cycles += m == "d"
            prev = m
    full_i = next((i for i in range(1, n + 1) if acts[i - 1] == "C" and level[i] >= emax - 0.1), None)

    mode_name = {"C": "charge", "D": "discharge", "S": "export", "-": "grid"}
    soc_steps = []
    for i in range(n):
        if i == 0 or acts[i] != acts[i - 1]:
            soc_steps.append({"time": local(slot_ts(i)).isoformat(), "label": fmt_at(slot_ts(i)),
                              "soc": soc_fc[i], "mode": mode_name[acts[i]]})
    soc_steps.append({"time": local(end_ts).isoformat(), "label": fmt_at(end_ts), "soc": soc_fc[n], "mode": "end"})

    mode_txt = {"charge": f"charging {charge_w} W", "discharge": "discharging", "idle": "idle"}[mode]
    est = " (estimated)" if avg_cost is None else ""
    report: list[dict[str, Any]] = [
        {"icon": "🔋", "title": f"Battery: {capacity:.1f} kWh (SOC now {soc:.0f}%, max {s.max_soc:g}%)"},
        {"icon": "📅", "title": f"Planning until {fmt_at(end_ts)}"},
        {"icon": "▶️", "title": f"Now: {mode_txt} (price €{cur.price:.3f})"},
        {"icon": "🏷️", "title": f"Stored energy cost: €{cost:.3f}/kWh ({e0:.1f} kWh){est}"},
        {"icon": "♻️", "title": f"Stored energy value: €{value_now:.3f}/kWh"},
    ]
    if charge_r:
        report.append({
            "icon": "⬇️", "kind": "charge",
            "title": f"Charging ({charge_q} quarters" + (f", {cycles} cycles" if cycles > 1 else "") + f", avg. {avg_charge_w} W)",
            "items": [fmt_range(r) for r in charge_r],
            "sub": f"{sum(c_in):.1f} kWh planned, avg. €{grid_cost / grid_in:.3f}/kWh"
                   + (f", full at {fmt_at(slot_ts(full_i))}" if full_i else ""),
        })
    else:
        report.append({"icon": "⬇️", "kind": "charge", "title": "Charging: not needed within the horizon"})
    if discharge_r:
        sub = f"{out_kwh:.1f} kWh"
        if self_kwh > 0.01:
            sub += f", avg. €{avoided / self_kwh:.3f}/kWh, avoided purchase €{avoided:.2f}"
        if export_kwh > 0.01:
            sub += f"; {export_kwh:.1f} kWh exported, revenue €{export_rev:.2f}"
        report.append({
            "icon": "🔴", "kind": "discharge",
            "title": "Discharging",
            "items": [fmt_range(r) for r in discharge_r],
            "sub": sub,
        })
    else:
        report.append({"icon": "🔴", "kind": "discharge", "title": "Discharging: no quarters above the discharge price"})
    # selling: planned export, or (selling off) the quarters above the sell price
    sell_ref = [sl.quote for sl in slots]
    if s.allow_export:
        if sell_r:
            report.append({
                "icon": "💰", "kind": "sell",
                "title": f"Selling (export > €{sell_price:.3f})",
                "items": [fmt_range(r) for r in sell_r],
                "sub": f"{export_kwh:.1f} kWh, avg. €{export_rev / export_kwh:.3f}/kWh, revenue €{export_rev:.2f}",
            })
        else:
            report.append({"icon": "💰", "kind": "sell",
                           "title": f"Selling: no quarters above €{sell_price:.3f}"})
    else:
        above = [i for i in range(n) if sell_ref[i] is not None and sell_ref[i] >= sell_price]
        if above:
            top = max(above, key=lambda i: sell_ref[i])
            report.append({
                "icon": "💰", "kind": "sell",
                "title": f"Sell above €{sell_price:.3f} (selling is off)",
                "items": [fmt_range(r) for r in ranges_where(lambda i: i in set(above))],
                "sub": f"{len(above)} quarters, highest €{sell_ref[top]:.3f} at {fmt_at(slot_ts(top))}",
            })
        else:
            best = max(range(n), key=lambda i: sell_ref[i] if sell_ref[i] is not None else -1e9)
            report.append({
                "icon": "💰", "kind": "sell",
                "title": f"Selling: no quarters above €{sell_price:.3f}",
                "sub": f"highest price €{sell_ref[best]:.3f} at {fmt_at(slot_ts(best))}",
            })
    report.append({
        "icon": "💵", "kind": "savings",
        "title": f"Expected savings: €{expected_savings:.2f} (until {fmt_at(end_ts)})",
        "sub": f"revenue €{revenue:.2f} − charging cost €{grid_cost:.2f}"
               + (f" {'+' if stock_delta >= 0 else '−'} {abs(stock_delta):.1f} kWh stored × €{cost:.3f}"
                  if abs(stock_delta) > 0.05 else "")
               + f"; after wear €{expected_savings - expected_wear:.2f}",
    })
    report.append({
        "icon": "📈", "kind": "soc",
        "title": f"Expected battery (consumption {load_total:.1f} kWh, of which {self_kwh:.1f} kWh from the battery)",
        "sub": f"Lowest: {soc_fc[soc_min_i]:.0f}% at {fmt_at(slot_ts(soc_min_i))}",
    })
    report.append({
        "icon": "💶", "title": f"Discharge price now: €{discharge_price:.3f}/kWh",
        "sub": f"€{value_now:.3f} value + {s.wear_ct:g} ct wear cost & discharge margin",
    })
    report.append({
        "icon": "💰", "title": f"Sell price: €{sell_price:.3f}/kWh",
        "sub": f"(€{cost:.3f} stored energy cost incl. {eff * 100:.0f}% efficiency + {s.wear_ct:g} ct wear cost & discharge margin) × "
               f"{1 + s.profit_pct / 100:.2f}, profit {s.profit_pct:g}%"
               + ("" if s.allow_export else " — selling is off"),
    })

    # plain text (same content)
    lines: list[str] = []
    for r in report:
        lines.append(f"{r['icon']} {r['title']}")
        if r.get("items"):
            lines.append(", ".join(r["items"]))
        if r.get("kind") == "soc":
            lines += [f"{st['label']} {st['soc']:.0f}% {st['mode']}" for st in soc_steps[:12]]
            if len(soc_steps) > 12:
                lines.append(f"… {soc_steps[-1]['label']} {soc_steps[-1]['soc']:.0f}% (end)")
        if r.get("sub"):
            lines.append(f"({r['sub']})")

    def iso(ts: float) -> str:
        return local(ts).isoformat()

    def next_action() -> str:
        if mode != "idle":
            return mode
        nxt = next((i for i in range(n) if acts[i] != "-"), None)
        if nxt is None:
            return "idle"
        return f"{mode_name[acts[nxt]]} at {fmt_at(slots[nxt].start)}"

    return {
        "mode": mode,
        "next_action": next_action(),
        "charge_power": charge_w,
        "discharge_power": discharge_w,
        "price_now": cur.price,
        "sell_price_now": cur.quote,
        "discharge_price": discharge_price,
        "energy_value": round(value_now, 4),
        "sell_price": round(sell_price, 4),
        "sell_base": round(sell_base, 4),
        "profit_pct": s.profit_pct,
        "allow_export": s.allow_export,
        "cost": round(cost, 4),
        "stored_kwh": round(e0, 3),
        "soc_end": soc_fc[-1],
        "soc_min": soc_fc[soc_min_i],
        "prices_known_until": iso(end_ts),
        "planned_grid_charge_kwh": round(grid_in, 3),
        "planned_charge_cost": round(grid_cost, 2),
        "planned_discharge_kwh": round(out_kwh, 3),
        "planned_export_kwh": round(export_kwh, 3),
        "planned_revenue": round(revenue, 2),
        "expected_savings": round(expected_savings, 2),
        "expected_savings_after_wear": round(expected_savings - expected_wear, 2),
        "charge_ranges": [{"start": iso(a), "end": iso(b)} for a, b in charge_r],
        "discharge_ranges": [{"start": iso(a), "end": iso(b)} for a, b in discharge_r],
        "report": report,
        "soc_steps": soc_steps,
        "text": "\n".join(lines),
        # Per kwartier vanaf 'start'; de eerste 'now' kwartieren zijn verleden (alleen prijs).
        "series": {
            "start": iso(past[0] if past else slots[0].start),
            "now": len(past),
            "plan": "." * len(past) + "".join(acts),  # C charge, D discharge, S export, - idle
            "price": [round(prices[t], 4) for t in past] + [round(sl.price, 4) for sl in slots],
            "threshold": [None] * len(past) + [round(t, 4) for t in thresholds],
            "soc": [None] * len(past) + soc_fc,
        },
    }
