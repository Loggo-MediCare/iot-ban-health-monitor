"""Rehabilitation sub-system scheduling (chapter 5.7.4).

The chapter notes a conflict: several patients need the same rehabilitation device at the same
site. This module turns treatment requests into a conflict-free timetable. It is a greedy list
scheduler: higher clinical priority first, then earliest deadline. Each session goes on the
compatible device that can start it earliest within the request's window.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Device:
    name: str
    kind: str  # e.g. "lower_limb_trainer", "treadmill", "upper_limb_robot"
    site: str


@dataclass(frozen=True)
class Request:
    patient: str
    kind: str
    site: str
    duration_min: int
    earliest_min: int
    deadline_min: int  # session must END by this time
    priority: int = 1  # higher first


@dataclass(frozen=True)
class Session:
    patient: str
    device: str
    start_min: int
    end_min: int


@dataclass
class Timetable:
    sessions: list[Session] = field(default_factory=list)
    unscheduled: list[Request] = field(default_factory=list)


def schedule(requests: list[Request], devices: list[Device]) -> Timetable:
    busy: dict[str, list[tuple[int, int]]] = {d.name: [] for d in devices}
    patient_busy: dict[str, list[tuple[int, int]]] = {}
    out = Timetable()
    for req in sorted(requests, key=lambda r: (-r.priority, r.deadline_min, r.earliest_min)):
        best: tuple[int, str] | None = None
        for d in devices:
            if d.kind != req.kind or d.site != req.site:
                continue
            start = _earliest_slot(busy[d.name] + patient_busy.get(req.patient, []),
                                   req.earliest_min, req.duration_min)
            if start + req.duration_min <= req.deadline_min and (best is None or start < best[0]):
                best = (start, d.name)
        if best is None:
            out.unscheduled.append(req)
            continue
        start, dev = best
        iv = (start, start + req.duration_min)
        busy[dev].append(iv)
        patient_busy.setdefault(req.patient, []).append(iv)
        out.sessions.append(Session(req.patient, dev, *iv))
    out.sessions.sort(key=lambda s: (s.device, s.start_min))
    return out


def _earliest_slot(taken: list[tuple[int, int]], earliest: int, dur: int) -> int:
    t = earliest
    for s, e in sorted(taken):
        if t + dur <= s:
            break
        t = max(t, e)
    return t


def conflicts(t: Timetable) -> list[tuple[Session, Session]]:
    """Overlapping sessions on one device or for one patient (should always be empty)."""
    bad = []
    ss = t.sessions
    for i, a in enumerate(ss):
        for b in ss[i + 1:]:
            if (a.device == b.device or a.patient == b.patient) and a.start_min < b.end_min and b.start_min < a.end_min:
                bad.append((a, b))
    return bad
