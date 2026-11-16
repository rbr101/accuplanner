"""Planner tests: pure Python (numpy + pyyaml), no Home Assistant needed."""

from datetime import datetime
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest
import yaml

_PATH = Path(__file__).parent.parent / "custom_components" / "accu_planner" / "planner.py"
_spec = importlib.util.spec_from_file_location("accu_planner_planner", _PATH)
P = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = P
_spec.loader.exec_module(P)

TZ = ZoneInfo("Europe/Amsterdam")
PROFILE = P.parse_profile("07-10=1.5; 17-19=2.5; 19-22=1.5; 22-07=1.0; *=0.4")[0]


@pytest.fixture(scope="module")
def prices():
    attrs = yaml.safe_load((Path(__file__).parent / "prices_2026_11_03.yaml").read_text())
    return P.read_prices(attrs, TZ)


def _settings(**kw):
    base = dict(efficiency=0.85, wear_ct=3, charge_w=3300, discharge_w=3500, min_soc=5, max_soc=100,
                allow_export=True, profit_pct=75)
    base.update(kw)
    return P.Settings(**base)


def _plan(prices, soc=99, **kw):
    now = datetime(2026, 11, 3, 13, 51, tzinfo=TZ).timestamp()
    return P.plan(now, prices, 21.4, soc, PROFILE, None, TZ, _settings(**kw), 0.464)


def test_read_prices(prices):
    assert len(prices) == 192
    first = datetime(2026, 11, 3, 0, 0, tzinfo=TZ).timestamp()
    assert prices[int(first)] == pytest.approx(0.40)


def test_parse_profile():
    prof, bad = P.parse_profile("07-10=1.5; 17:30-19=2.5; *=0.4")
    assert not bad
    assert P.profile_kw(prof, datetime(2026, 11, 3, 8, 0, tzinfo=TZ)) == 1.5
    assert P.profile_kw(prof, datetime(2026, 11, 3, 18, 0, tzinfo=TZ)) == 2.5
    assert P.profile_kw(prof, datetime(2026, 11, 3, 12, 0, tzinfo=TZ)) == 0.4
    assert P.parse_profile("nonsense")[1]


def test_discharge_price_is_replacement_value(prices):
    """Discharge price = cost of recharging later (0.37 / 0.85) + wear, not the stored cost (0.464)."""
    r = _plan(prices)
    assert r["energy_value"] == pytest.approx(0.435, abs=0.002)
    assert r["discharge_price"] == pytest.approx(0.465, abs=0.002)
    # discharging at 0.48 pays off because recharging at <= 0.37 is possible tomorrow
    assert r["discharge_ranges"][0]["start"] == "2026-11-03T15:45:00+01:00"


def test_charges_only_the_cheapest_quarters(prices):
    r = _plan(prices)
    assert 0 < r["planned_grid_charge_kwh"] < 6
    for rng in r["charge_ranges"]:
        assert rng["start"].startswith("2026-11-04")


def test_sell_price_modes(prices):
    on = _plan(prices, sell_on_value=True)
    off = _plan(prices, sell_on_value=False)
    assert off["sell_price"] == pytest.approx((0.464 + 0.03) * 1.75, abs=0.001)
    assert on["sell_price"] == pytest.approx(on["discharge_price"] * 1.75, abs=0.001)
    low = _plan(prices, sell_on_value=True, profit_pct=10)
    assert "S" in low["series"]["plan"]


def test_no_export_when_not_allowed(prices):
    r = _plan(prices, allow_export=False, profit_pct=0)
    assert "S" not in r["series"]["plan"]


def test_savings_split_over_quarters():
    st = P.CostState(avg=0.30, kwh=10, t=600)
    quarter_prices = {0: 0.40, 900: 0.50}
    saved = st.step(9, 1200, lambda d, h, t: 0.4, lambda ts: quarter_prices.get(int(ts // 900 * 900)))
    assert saved == (pytest.approx(0.15), 1)


def test_savings_ledger_periods():
    led = P.SavingsLedger()
    now = datetime(2026, 11, 3, 12, 0, tzinfo=TZ).timestamp()
    led.add(now, 1.0, 2.0, TZ)
    led.add(now - 86400, 0.5, 1.0, TZ)
    s = led.summary(now, TZ)
    assert s["today"]["eur"] == 1.0
    assert s["yesterday"]["eur"] == 0.5
    assert s["total"] == 1.5


def test_estimate_after_known_prices():
    """Only today's prices known, cheap night behind us: with the estimate the battery is used at ~0.40."""
    from datetime import timedelta

    day = datetime(2026, 11, 16, 0, 0, tzinfo=TZ)
    prices = {}
    for i in range(96):
        h = i / 4
        p = min(0.40, 0.26 + 0.02 * abs(h - 4)) if h < 7 else (0.46 if 17.5 <= h < 20 else 0.40)
        prices[int((day + timedelta(minutes=15 * i)).timestamp())] = round(p, 3)
    now = datetime(2026, 11, 16, 8, 11, tzinfo=TZ).timestamp()

    def run(est):
        return P.plan(now, prices, 21.5, 100, PROFILE, None, TZ, _settings(estimate_after=est), 0.313)

    off, on = run(False), run(True)
    assert off["discharge_price"] > 0.46  # only the known (expensive) quarters to refill: battery stays full
    assert not off["discharge_ranges"]
    assert on["discharge_price"] == pytest.approx(0.40, abs=0.005)
    assert on["discharge_ranges"]
    assert on["planned_grid_charge_kwh"] == 0  # estimates are never used to plan charging
