import json
import random

import pytest

from banmon.cloud import CloudServer
from banmon.gateway import Gateway, adc_quantise
from banmon.gsm import CTRL_Z, Modem, Sim800Simulator
from banmon.rehab import Device, Request, conflicts, schedule
from banmon.sensors import Reading
from banmon.sim import Config, run
from banmon.vitals import Level, classify


# --- thresholds (chapter values) -------------------------------------------------------
@pytest.mark.parametrize("vital,value,level", [
    ("spo2", 97, Level.NORMAL), ("spo2", 93, Level.WARNING), ("spo2", 89, Level.CRITICAL),
    ("systolic", 115, Level.NORMAL), ("systolic", 125, Level.WARNING), ("systolic", 185, Level.CRITICAL),
    ("diastolic", 85, Level.WARNING), ("resp_rate", 16, Level.NORMAL), ("resp_rate", 22, Level.WARNING),
    ("glucose", 50, Level.CRITICAL),
])
def test_classify(vital, value, level):
    assert classify(vital, value) == level


def test_adc_quantise_is_within_one_lsb_and_clamps():
    assert abs(adc_quantise("spo2", 97.3) - 97.3) <= 50 / 1023
    assert adc_quantise("temp_c", 99) == 45


# --- GSM AT driver -----------------------------------------------------------------------
def _modem(**kw):
    sim = Sim800Simulator(random.Random(0), loss_prob=0.0, **kw)
    return sim, Modem(sim)


def test_sms_via_at_commands():
    sim, m = _modem()
    assert m.ready()
    assert m.send_sms("+123", "hello")
    assert sim.sms_outbox == [(0, "+123", "hello")]


def test_sms_fails_without_coverage():
    sim, m = _modem(outages_min=((0, 10),))
    assert not m.ready()
    assert not m.send_sms("+123", "x")


def test_http_post_round_trips_json():
    sim, m = _modem()
    assert m.http_post_json({"a": [1, 2]})
    assert json.loads(sim.http_posted[0][1]) == {"a": [1, 2]}


def test_unknown_at_command_errors():
    sim, _ = _modem()
    sim.write("AT+BOGUS")
    assert sim.read_response() == "ERROR"
    sim.write("AT+CMGS=\"+1\"")  # not in text mode yet
    assert sim.read_response() == "ERROR"
    assert CTRL_Z == "\x1a"


# --- gateway -----------------------------------------------------------------------------
def _gw(confirm_n=2, **kw):
    sim, m = _modem(**kw)
    return sim, Gateway(0, m, confirm_n=confirm_n)


def test_single_spo2_glitch_is_debounced():
    sim, gw = _gw()
    for t, v in enumerate([97, 70, 97, 97]):
        gw.ingest(Reading(0, t, "spo2", v))
        gw.tick(t)
    assert not gw.alerts and not sim.sms_outbox


def test_sustained_desaturation_sends_three_sms():
    sim, gw = _gw()
    for t, v in enumerate([97, 85, 85, 85]):
        gw.ingest(Reading(0, t, "spo2", v))
        gw.tick(t)
    assert len(gw.alerts) == 1
    assert {n for _, n, _ in sim.sms_outbox} == {gw.contacts.doctor, gw.contacts.emergency, gw.contacts.relative}
    assert len(sim.sms_outbox) == 3  # not repeated while still abnormal


def test_warning_escalates_to_critical():
    sim, gw = _gw()
    for t, v in enumerate([97, 93, 93, 88, 88]):
        gw.ingest(Reading(0, t, "spo2", v))
        gw.tick(t)
    assert sorted(a["level"] for a in gw.alerts.values()) == [1, 2]


def test_implausible_jump_on_slow_vital_needs_confirmation():
    sim, gw = _gw()
    gw.ingest(Reading(0, 0, "temp_c", 36.8))
    gw.ingest(Reading(0, 5, "temp_c", 42.5))  # probe artefact
    gw.ingest(Reading(0, 10, "temp_c", 36.8))
    assert not gw.alerts
    for t, v in ((15, 37.6), (20, 38.4), (25, 39.2)):  # plausible 0.8 degC / 5 min rise
        gw.ingest(Reading(0, t, "temp_c", v))
    assert sorted((a["raised_t"], a["level"]) for a in gw.alerts.values()) == [(20, 1), (25, 2)]


def test_confirmed_jump_still_alerts():
    sim, gw = _gw()
    for t, v in ((0, 36.8), (5, 40.0), (10, 40.1)):  # real sudden fever spike, confirmed next sample
        gw.ingest(Reading(0, t, "temp_c", v))
    assert [a["raised_t"] for a in gw.alerts.values()] == [10]


def test_sms_retried_after_outage():
    sim, gw = _gw(outages_min=((0, 5),))
    for t in range(8):
        sim.now_min = t
        gw.ingest(Reading(0, t, "spo2", 85))
        gw.tick(t)
    (alert,) = gw.alerts.values()
    assert alert["notified_t"] == 5


# --- cloud -------------------------------------------------------------------------------
def test_cloud_stores_readings_and_upserts_alerts():
    c = CloudServer()
    a = {"id": "0-spo2-3", "patient": 0, "vital": "spo2", "value": 85.0, "level": 2, "raised_t": 3, "notified_t": None}
    c.ingest(5, json.dumps({"patient": 0, "readings": [[3, "spo2", 85.0, 2]], "alerts": [a]}))
    c.ingest(10, json.dumps({"patient": 0, "readings": [], "alerts": [dict(a, notified_t=6)]}))
    (row,) = c.alerts()
    assert row["notified_t"] == 6 and row["channel"].startswith("sms")
    assert c.history(0, "spo2") == [(3, 85.0)]


# --- end to end --------------------------------------------------------------------------
def test_end_to_end_small_cohort():
    s = run(Config(patients=15, duration_min=480, episode_rate_per_day=6, seed=3)).score()
    assert s["critical"]["sensitivity"] == 1.0
    assert s["readings_stored"] == s["readings_generated"]  # store-and-forward loses nothing


# --- rehab scheduling (5.7.4) ------------------------------------------------------------
def test_rehab_schedule_has_no_conflicts_and_respects_priority():
    devs = [Device("LL-1", "lower_limb_trainer", "A")]
    reqs = [Request("p1", "lower_limb_trainer", "A", 60, 0, 60, priority=1),
            Request("p2", "lower_limb_trainer", "A", 60, 0, 60, priority=3),
            Request("p3", "lower_limb_trainer", "A", 30, 0, 200)]
    tt = schedule(reqs, devs)
    assert not conflicts(tt)
    assert [s.patient for s in tt.sessions] == ["p2", "p3"]
    assert [r.patient for r in tt.unscheduled] == ["p1"]
