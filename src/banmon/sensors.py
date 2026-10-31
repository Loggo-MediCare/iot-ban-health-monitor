"""Synthetic body-area-network sensors with injected ground-truth episodes and artefacts."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .vitals import SAMPLE_PERIOD_MIN, SENSOR_RANGE, Level, classify

BASELINE = {"spo2": 97.5, "systolic": 112, "diastolic": 72, "resp_rate": 15, "heart_rate": 72,
            "temp_c": 36.7, "glucose": 110}
NOISE_SD = {"spo2": 0.6, "systolic": 4, "diastolic": 3, "resp_rate": 1.0, "heart_rate": 3,
            "temp_c": 0.1, "glucose": 6}

# Episode templates: (vital, value the vital ramps to)
EPISODES = {
    "desaturation": ("spo2", 86.0),
    "hypertensive_crisis": ("systolic", 190.0),
    "hypotension": ("systolic", 65.0),
    "tachypnoea": ("resp_rate", 29.0),
    "tachycardia": ("heart_rate", 140.0),
    "fever": ("temp_c", 39.6),
    "hypoglycaemia": ("glucose", 45.0),
    "hyperglycaemia": ("glucose", 290.0),
    # warning-level episodes
    "mild_hypertension": ("systolic", 138.0),
    "mild_desaturation": ("spo2", 92.5),
}


@dataclass(frozen=True)
class Episode:
    patient: int
    kind: str
    start_min: int
    duration_min: int

    @property
    def vital(self) -> str:
        return EPISODES[self.kind][0]

    @property
    def target(self) -> float:
        return EPISODES[self.kind][1]

    @property
    def level(self) -> Level:
        return classify(self.vital, self.target)

    @property
    def end_min(self) -> int:
        return self.start_min + self.duration_min


@dataclass(frozen=True)
class Reading:
    patient: int
    t_min: int
    vital: str
    value: float


@dataclass
class Patient:
    pid: int
    rng: random.Random
    episodes: list[Episode] = field(default_factory=list)
    artefact_prob: float = 0.003  # per sample: motion artefact / probe slip

    def __post_init__(self):
        self.baseline = {v: b + self.rng.gauss(0, NOISE_SD[v]) for v, b in BASELINE.items()}
        self._ar = {v: 0.0 for v in BASELINE}

    def _episode_offset(self, vital: str, t: int) -> float:
        for ep in self.episodes:
            if ep.vital == vital and ep.start_min <= t < ep.end_min:
                ramp = min(1.0, (t - ep.start_min + 1) / 5.0)  # reach target over 5 min
                return ramp * (ep.target - self.baseline[vital])
        return 0.0

    def sample(self, vital: str, t: int) -> float:
        self._ar[vital] = 0.8 * self._ar[vital] + self.rng.gauss(0, NOISE_SD[vital] * 0.6)
        v = self.baseline[vital] + self._ar[vital] + self._episode_offset(vital, t)
        if self.rng.random() < self.artefact_prob:  # single-sample glitch, e.g. SpO2 probe slip
            lo, hi = SENSOR_RANGE[vital]
            v = self.rng.choice((lo + (hi - lo) * 0.15, hi - (hi - lo) * 0.15))
        if vital == "spo2":
            v = min(v, 100.0)
        return v

    def readings_at(self, t: int) -> list[Reading]:
        return [Reading(self.pid, t, v, self.sample(v, t))
                for v, period in SAMPLE_PERIOD_MIN.items() if t % period == 0]


def make_cohort(n_patients: int, duration_min: int, episode_rate_per_day: float,
                seed: int) -> tuple[list[Patient], list[Episode]]:
    rng = random.Random(seed)
    patients, episodes = [], []
    for pid in range(n_patients):
        p = Patient(pid, random.Random(rng.random()))
        n_ep = sum(rng.random() < episode_rate_per_day * duration_min / 1440 / 4 for _ in range(4))
        busy: list[tuple[int, int]] = []
        for _ in range(n_ep):
            dur = rng.randint(15, 60)
            start = rng.randint(10, max(11, duration_min - dur - 10))
            if any(start < e + 30 and start + dur + 30 > s for s, e in busy):
                continue  # keep episodes for one patient apart so ground truth is unambiguous
            busy.append((start, start + dur))
            ep = Episode(pid, rng.choice(sorted(EPISODES)), start, dur)
            p.episodes.append(ep)
            episodes.append(ep)
        patients.append(p)
    return patients, episodes
