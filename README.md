# IoT Body-Area-Network Health Monitor

> **SIMULATION ONLY. NOT A MEDICAL DEVICE.** Synthetic patients. The alert thresholds are not
> clinically validated. No real modem or sensor is attached.

A working prototype of the system in Chapter 5, "Overview of the architecture and applications of
IoT-based healthcare systems" (Figs. 5.1 and 5.2):

```
BAN sensors ──► Raspberry Pi gateway ──► GSM modem (AT commands) ──► cloud server + SQLite
 (SpO2, BP, RR,   10-bit ADC, thresholds,      SMS: doctor / emergency /     store all readings,
  HR, temp, CGM)  debounce, plausibility,      next of kin (critical)        dashboard push (warning),
                  store-and-forward            HTTP: batched readings        patient history query
```

| Module | Chapter | What it does |
|---|---|---|
| `vitals.py` | 5.5.1–5.5.6 | Alert thresholds. Each records its source: SpO2, BP and RR come from the chapter; HR, temperature and glucose follow NEWS2 / CGM consensus and are marked "external" |
| `sensors.py` | 5.4, 5.5 | Synthetic patients with ground-truth episodes (desaturation, hypertensive crisis, hypoglycaemia, ...) and probe-slip artefacts |
| `gateway.py` | 5.3, 5.3.1 | Pi logic: ADC, classification, `confirm_n` debounce, rate-of-change plausibility check, direct SMS for critical alerts, store-and-forward upload |
| `gsm.py` | 5.3 | AT-command driver (`AT`, `AT+CSQ`, `AT+CMGF`, `AT+CMGS`, `AT+HTTP*`) over a pluggable transport. Ships with a SIM800 simulator; `PySerialTransport` targets a real modem (untested, no hardware) |
| `cloud.py` | 5.2, 5.3.3 | Relational store. Normal data is stored only; warnings go to the doctor's dashboard; critical-alert delivery is recorded |
| `rehab.py` | 5.7.4 | Conflict-free scheduling of shared rehabilitation devices (priority, then deadline) |

## Results (100 patients, 24 h, seed 7; `python -m banmon sweep`)

| Setting | Critical episodes detected | Median / max notify latency | False alerts (critical) | SMS sent |
|---|---|---|---|---|
| no debounce (`confirm_n=1`) | 108/108 | 7 / 26 min | 3,079 (650) | 2,286 |
| **default (`confirm_n=2` + plausibility)** | **108/108** | **7 / 26 min** | **1,433 (5)** | **345** |
| 2 h GSM outage | 108/108 | 7 / **106** min | 1,433 (5) | 345 |
| 20 % upload loss | 108/108 | 7 / 26 min | 1,433 (5) | 345 |

* Debouncing and the plausibility check reduce false critical SMS from 650 to 5 without missing
  any critical episode. The cost is about 2 min of median latency. Without debouncing there are
  roughly 6× more SMS.
* Store-and-forward keeps every reading (547,200/547,200), even with 20 % upload loss or a 2 h
  outage. During the outage, critical SMS are queued and wait for coverage. If outages last longer,
  a second channel (such as LoRa or Wi-Fi) would be needed.
* **Alarm fatigue from the chapter's thresholds.** The remaining ~1,400 false alerts are all
  warnings. Systolic and diastolic account for 1,038 of them, because the chapter treats anything
  above 120/80 as a danger signal. Respiration rate adds 371, because patients whose resting rate
  is near 12 /min keep crossing the 12–20 band. Per-patient baselines would be the next
  improvement.
* One warning episode in 34 was missed: a mild desaturation that stayed close to the 95 % limit.

## Setup

```bash
cd D:\robot\iot-ban-health-monitor
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m banmon rules      # thresholds + sources
.venv\Scripts\python -m banmon run --patients 50 --hours 12
.venv\Scripts\python -m banmon sweep      # table above (~90 s)
```

**Hardware check (`docs/device-review.md`):** SIM800-class modems are 2G-only. Taiwan shut down 2G in
2017 and 3G at the end of 2024, so use an LTE Cat-M1/NB-IoT module (AT-command compatible) instead. Of the Chapter 11 MEMS
devices, only the accelerometer/gyroscope is commercially available and directly useful here, for fall detection and
for gating motion artefacts. The implantable sensors are research prototypes.

To move to hardware, replace `Sim800Simulator` with `PySerialTransport("/dev/serial0")`
(`pip install .[serial]`) and replace `sensors.Patient` with real sensor drivers. Neither path has
been tested here.
