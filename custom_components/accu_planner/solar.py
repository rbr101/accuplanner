"""Laden bij zonne-overschot: integrerende regelaar op de netmeting.

Zelfde werking als de Node-RED-flow 'Solar over prod (AVG)':
  - elke 3 s: fout = teruglevering - afname (W), alleen als de PV produceert;
  - elke 15 s: stap = 0,6 x gemiddelde fout, begrensd op +400 / -1000 W;
    doel = doel + stap, begrensd op 0 .. max; uitvoer 0 onder 200 W, anders
    afgerond op 10 W.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SAMPLE_INTERVAL_S = 3
CONTROL_INTERVAL_S = 15
GAIN = 0.6
MAX_STEP_UP = 400
MAX_STEP_DOWN = 1000
ROUND_STEP = 10
MIN_CHANGE_W = 50  # kleinere wijzigingen niet naar de accu sturen


@dataclass
class SurplusRegulator:
    """Integrerende regelaar: laadvermogen volgt het zonne-overschot."""

    min_out: float = 200
    max_out: float = 3500
    target: float = 0.0
    samples: list[float] = field(default_factory=list)
    last_error: float | None = None
    last_step: float = 0.0

    def add_sample(self, error_w: float) -> None:
        """Fout in W: positief = teruglevering (overschot)."""
        self.samples.append(error_w)

    def step(self) -> int:
        """Eén regelstap (elke 15 s). Geeft het gewenste laadvermogen in W."""
        if not self.samples:
            self.target = 0.0
            self.last_error = None
            self.last_step = 0.0
            return 0
        avg = sum(self.samples) / len(self.samples)
        self.samples.clear()
        step = max(-MAX_STEP_DOWN, min(MAX_STEP_UP, round(avg * GAIN)))
        self.target = max(0.0, min(self.max_out, self.target + step))
        self.last_error = avg
        self.last_step = step
        return self.output

    @property
    def output(self) -> int:
        if self.target < self.min_out:
            return 0
        return int(round(self.target / ROUND_STEP) * ROUND_STEP)

    def reset(self) -> None:
        self.target = 0.0
        self.samples.clear()


# ---------------------------------------------------------------- ontladen
DIS_SAMPLE_INTERVAL_S = 3
DIS_CONTROL_INTERVAL_S = 20
DIS_GAIN = 0.25
DIS_ROUND_STEP = 50
DIS_MIN_CHANGE_W = 100
DIS_STUCK_CYCLES = 5


@dataclass
class DischargeRegulator:
    """Ontlaadvermogen volgt de netafname (afname naar 0), zoals de Node-RED-flow
    'Calculate Battery discharge power':
      - elke 3 s: fout = afname - teruglevering (W);
      - elke 20 s: doel += 0,25 x gemiddelde fout, begrensd op 0 .. max;
        5 cycli tegen een grens -> doel opnieuw gelijk aan de huidige accu-instelling;
        uitvoer 0 onder het minimum (100 W), anders afgerond op 50 W.
    """

    min_out: float = 100
    max_out: float = 3500
    target: float = 0.0
    samples: list[float] = field(default_factory=list)
    clamped_streak: int = 0
    last_error: float | None = None

    def add_sample(self, error_w: float) -> None:
        """Fout in W: positief = afname van het net (accu moet meer leveren)."""
        self.samples.append(error_w)

    def step(self, current_setting: float | None = None) -> int | None:
        """Eén regelstap (elke 20 s). None = geen metingen (niets veranderen)."""
        if not self.samples:
            return None
        avg = sum(self.samples) / len(self.samples)
        self.samples.clear()
        self.last_error = avg
        value = self.target + round(avg * DIS_GAIN)
        clamped = value < 0 or value > self.max_out
        value = max(0.0, min(self.max_out, value))
        if clamped:
            self.clamped_streak += 1
            if self.clamped_streak >= DIS_STUCK_CYCLES and current_setting is not None:
                value = max(0.0, min(self.max_out, current_setting))
                self.clamped_streak = 0
        else:
            self.clamped_streak = 0
        self.target = value
        return self.output

    @property
    def output(self) -> int:
        if self.target < self.min_out:
            return 0
        return int(round(self.target / DIS_ROUND_STEP) * DIS_ROUND_STEP)

    def reset(self) -> None:
        self.target = 0.0
        self.samples.clear()
        self.clamped_streak = 0
