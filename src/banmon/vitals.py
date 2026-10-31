"""Vital-sign alert thresholds.

Each rule records where its numbers come from. Chapter 5 gives only some of them (SpO2,
blood pressure, respiration rate). The rest follow published early-warning conventions and are
marked "external". None of these thresholds are clinically validated for any patient.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Level(IntEnum):
    NORMAL = 0
    WARNING = 1
    CRITICAL = 2


@dataclass(frozen=True)
class Rule:
    vital: str
    unit: str
    critical_low: float | None
    warning_low: float | None
    warning_high: float | None
    critical_high: float | None
    source: str

    def classify(self, value: float) -> Level:
        if self.critical_low is not None and value < self.critical_low:
            return Level.CRITICAL
        if self.critical_high is not None and value > self.critical_high:
            return Level.CRITICAL
        if self.warning_low is not None and value < self.warning_low:
            return Level.WARNING
        if self.warning_high is not None and value > self.warning_high:
            return Level.WARNING
        return Level.NORMAL


RULES: dict[str, Rule] = {r.vital: r for r in (
    Rule("spo2", "%", 90, 95, None, None,
         "chapter 5.5.1: healthy SpO2 > 95 %, < 90 % needs urgent clinical intervention"),
    Rule("systolic", "mmHg", 70, 90, 120, 180,
         "chapter 5.5.2: normal 90-120, above 120 is a danger signal; critical bounds external "
         "(hypertensive crisis >= 180, shock < 70)"),
    Rule("diastolic", "mmHg", 40, 60, 80, 120,
         "chapter 5.5.2: normal 60-80; critical bounds external (crisis >= 120)"),
    Rule("resp_rate", "/min", 8, 12, 20, 25,
         "chapter 5.5.3: relaxed normal 12-20 /min; critical bounds external (NEWS2 <= 8, >= 25)"),
    Rule("heart_rate", "bpm", 40, 50, 100, 130,
         "external: resting adult range; NEWS2-style critical bounds. Chapter Table 5.2 lists "
         "exercise zones, not resting limits"),
    Rule("temp_c", "degC", 35.0, 36.1, 38.0, 39.0,
         "external: NEWS2 temperature bands (chapter 5.5.5 gives no limits)"),
    Rule("glucose", "mg/dL", 54, 70, 180, 250,
         "external: CGM consensus target range 70-180, level-2 hypo < 54, marked hyper > 250. "
         "Chapter 5.5.6 gives the 5/10/15 min CGM intervals"),
)}

# Sampling period per vital, minutes. Chapter 5.5.6: CGM reports every 5, 10 or 15 min.
SAMPLE_PERIOD_MIN = {"spo2": 1, "systolic": 5, "diastolic": 5, "resp_rate": 1, "heart_rate": 1,
                     "temp_c": 5, "glucose": 5}

# Physical sensor range used for the gateway's 10-bit ADC model (lo, hi).
SENSOR_RANGE = {"spo2": (50, 100), "systolic": (40, 260), "diastolic": (20, 160), "resp_rate": (0, 60),
                "heart_rate": (20, 250), "temp_c": (30, 45), "glucose": (20, 500)}


def classify(vital: str, value: float) -> Level:
    return RULES[vital].classify(value)
