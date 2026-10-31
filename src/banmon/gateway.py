"""Raspberry Pi gateway (chapter 5.3, Fig. 5.2).

For each reading it:
1. quantises the analogue value through a 10-bit ADC model;
2. classifies it against the alert rules. A level only counts after ``confirm_n`` consecutive
   1-minute samples (debounce against probe slips), and the same vital is not re-alerted until
   it has been normal again for ``confirm_n`` samples;
3. sends CRITICAL alerts straight from the gateway by SMS to the doctor, emergency services and
   the next of kin, retrying every minute while there is no coverage;
4. buffers every reading and uploads a batch to the cloud every ``upload_period_min`` over GSM
   HTTP (store-and-forward when the upload fails).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gsm import Modem
from .sensors import Reading
from .vitals import SAMPLE_PERIOD_MIN, SENSOR_RANGE, Level, classify

ADC_BITS = 10

# Largest physiologically plausible change between two samples of a slow (5-min) vital. A bigger
# jump is held as "suspect" until the next sample confirms it (probe slip / cuff artefact filter).
MAX_STEP = {"systolic": 50.0, "diastolic": 30.0, "temp_c": 1.0, "glucose": 60.0}


def adc_quantise(vital: str, value: float) -> float:
    lo, hi = SENSOR_RANGE[vital]
    steps = (1 << ADC_BITS) - 1
    code = round((min(max(value, lo), hi) - lo) / (hi - lo) * steps)
    return lo + code * (hi - lo) / steps


@dataclass
class Contacts:
    doctor: str = "+10000000001"
    emergency: str = "+10000000112"
    relative: str = "+10000000002"


@dataclass
class _VitalState:
    abnormal_len: int = 0  # consecutive samples >= WARNING
    critical_len: int = 0  # consecutive CRITICAL samples
    normal_len: int = 0
    active: Level = Level.NORMAL  # level already alerted
    last: float | None = None  # previous sample (for the plausibility check)
    suspect: bool = False  # current abnormal run began with an implausible jump


@dataclass
class Gateway:
    patient: int
    modem: Modem
    contacts: Contacts = field(default_factory=Contacts)
    confirm_n: int = 2
    upload_period_min: int = 5
    max_buffer: int = 20000
    buffer: list[list] = field(default_factory=list)
    alerts: dict[str, dict] = field(default_factory=dict)
    dropped: int = 0
    _state: dict[str, _VitalState] = field(default_factory=dict)
    _sms_queue: list[tuple[str, str, str]] = field(default_factory=list)  # (alert id, number, text)
    _unsynced: set[str] = field(default_factory=set)  # alerts the cloud has not seen in this state

    def _needed(self, vital: str, st: _VitalState) -> int:
        if SAMPLE_PERIOD_MIN[vital] == 1:
            return self.confirm_n
        # Slow vitals (BP, temperature, CGM every 5 min): one sample suffices unless the abnormal
        # run started with an implausible jump, which must be confirmed by the next sample.
        return 2 if st.suspect else 1

    def ingest(self, r: Reading) -> None:
        value = adc_quantise(r.vital, r.value)
        level = classify(r.vital, value)
        self.buffer.append([r.t_min, r.vital, round(value, 2), int(level)])
        if len(self.buffer) > self.max_buffer:
            self.buffer.pop(0)
            self.dropped += 1

        st = self._state.setdefault(r.vital, _VitalState())
        if level >= Level.WARNING and st.abnormal_len == 0:  # an abnormal run starts here
            st.suspect = (r.vital in MAX_STEP and st.last is not None
                          and abs(value - st.last) > MAX_STEP[r.vital])
        st.last = value
        need = self._needed(r.vital, st)
        st.abnormal_len = st.abnormal_len + 1 if level >= Level.WARNING else 0
        st.critical_len = st.critical_len + 1 if level == Level.CRITICAL else 0
        st.normal_len = st.normal_len + 1 if level == Level.NORMAL else 0
        if st.normal_len >= need:
            st.active = Level.NORMAL
        confirmed = (Level.CRITICAL if st.critical_len >= need
                     else Level.WARNING if st.abnormal_len >= need else Level.NORMAL)
        if confirmed > st.active:
            st.active = confirmed
            self._raise(r.t_min, r.vital, value, confirmed)

    def _raise(self, t: int, vital: str, value: float, level: Level) -> None:
        aid = f"{self.patient}-{vital}-{t}"
        self.alerts[aid] = {"id": aid, "patient": self.patient, "vital": vital, "value": value,
                            "level": int(level), "raised_t": t, "notified_t": None}
        self._unsynced.add(aid)
        if level == Level.CRITICAL:
            text = f"CRITICAL patient {self.patient}: {vital}={value:.1f} at t={t} min"
            for number in (self.contacts.doctor, self.contacts.emergency, self.contacts.relative):
                self._sms_queue.append((aid, number, text))

    def tick(self, t: int) -> None:
        """Called once per simulated minute after that minute's readings."""
        pending = []
        for aid, number, text in self._sms_queue:
            if not self.modem.send_sms(number, text):
                pending.append((aid, number, text))
        done = {a for a, _, _ in self._sms_queue} - {a for a, _, _ in pending}
        for aid in done:
            self.alerts[aid]["notified_t"] = t
            self._unsynced.add(aid)
        self._sms_queue = pending

        if t % self.upload_period_min == 0 and (self.buffer or self._unsynced):
            payload = {"patient": self.patient, "sent_t": t, "readings": self.buffer,
                       "alerts": [self.alerts[a] for a in sorted(self._unsynced)]}
            if self.modem.http_post_json(payload):
                self.buffer = []
                self._unsynced.clear()
