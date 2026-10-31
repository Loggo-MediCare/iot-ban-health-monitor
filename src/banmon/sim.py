"""End-to-end run: cohort -> gateways -> simulated GSM -> cloud, scored against ground truth."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .cloud import CloudServer
from .gateway import Gateway
from .gsm import Modem, Sim800Simulator
from .sensors import Episode, make_cohort
from .vitals import Level


@dataclass(frozen=True)
class Config:
    patients: int = 100
    duration_min: int = 1440
    episode_rate_per_day: float = 1.5
    confirm_n: int = 2
    loss_prob: float = 0.01
    gsm_outages_min: tuple[tuple[int, int], ...] = ((600, 640),)
    artefact_prob: float = 0.003
    seed: int = 7


@dataclass
class Outcome:
    episodes: list[Episode]
    alerts: list[dict]
    readings_stored: int
    readings_generated: int
    sms_sent: int

    # --- scoring ---------------------------------------------------------------------
    GRACE_MIN = 10

    def _match(self, ep: Episode) -> list[dict]:
        return [a for a in self.alerts if a["patient"] == ep.patient and a["vital"] == ep.vital
                and ep.start_min <= a["raised_t"] <= ep.end_min + self.GRACE_MIN]

    def score(self) -> dict:
        rows = {"critical": [], "warning": []}
        for ep in self.episodes:
            m = self._match(ep)
            want = Level.CRITICAL if ep.level == Level.CRITICAL else Level.WARNING
            hit = [a for a in m if a["level"] >= want]
            lat = min((a["notified_t"] - ep.start_min for a in hit if a["notified_t"] is not None),
                      default=None)
            rows["critical" if want == Level.CRITICAL else "warning"].append(lat)
        matched_ids = {a["id"] for ep in self.episodes for a in self._match(ep)}
        false = [a for a in self.alerts if a["id"] not in matched_ids]
        out = {}
        for k, lats in rows.items():
            got = sorted(x for x in lats if x is not None)
            out[k] = {"episodes": len(lats), "detected": len(got),
                      "sensitivity": len(got) / len(lats) if lats else float("nan"),
                      "median_latency_min": got[len(got) // 2] if got else None,
                      "max_latency_min": got[-1] if got else None}
        out["false_alerts"] = len(false)
        out["false_critical"] = sum(a["level"] == 2 for a in false)
        by_vital: dict[str, list[int]] = {}
        for a in false:
            by_vital.setdefault(a["vital"], [0, 0])[a["level"] - 1] += 1
        out["false_by_vital"] = {v: {"warning": w, "critical": c} for v, (w, c) in sorted(by_vital.items())}
        out["readings_stored"] = self.readings_stored
        out["readings_generated"] = self.readings_generated
        out["sms_sent"] = self.sms_sent
        return out


def run(cfg: Config = Config()) -> Outcome:
    patients, episodes = make_cohort(cfg.patients, cfg.duration_min, cfg.episode_rate_per_day, cfg.seed)
    cloud = CloudServer()
    rng = random.Random(cfg.seed + 1)
    sims, gws = [], []
    for p in patients:
        p.artefact_prob = cfg.artefact_prob
        sim = Sim800Simulator(random.Random(rng.random()), cfg.loss_prob, cfg.gsm_outages_min)
        sims.append(sim)
        gws.append(Gateway(p.pid, Modem(sim), confirm_n=cfg.confirm_n))
    generated = 0
    # Run past the end so buffered data and pending SMS can drain.
    for t in range(cfg.duration_min + 30):
        for p, sim, gw in zip(patients, sims, gws):
            sim.now_min = t
            if t < cfg.duration_min:
                for r in p.readings_at(t):
                    gw.ingest(r)
                    generated += 1
            gw.tick(t)
            for sent_t, body in sim.http_posted:
                cloud.ingest(sent_t, body)
            sim.http_posted.clear()
    return Outcome(episodes, cloud.alerts(), cloud.reading_count(), generated,
                   sum(len(s.sms_outbox) for s in sims))
