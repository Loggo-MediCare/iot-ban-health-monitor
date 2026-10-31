"""Command line: ``python -m banmon {run,sweep,rules}``."""
from __future__ import annotations

import argparse
from dataclasses import replace

from .sim import Config, run
from .vitals import RULES


def _fmt(s: dict) -> str:
    c, w = s["critical"], s["warning"]
    return (f"critical {c['detected']}/{c['episodes']} (median {c['median_latency_min']} min, max {c['max_latency_min']}) | "
            f"warning {w['detected']}/{w['episodes']} (median {w['median_latency_min']} min) | "
            f"false alerts {s['false_alerts']} (critical {s['false_critical']}) | SMS {s['sms_sent']} | "
            f"stored {s['readings_stored']:,}/{s['readings_generated']:,}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="banmon", description="SIMULATION ONLY IoT BAN health monitor")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--patients", type=int, default=100)
    r.add_argument("--hours", type=float, default=24)
    r.add_argument("--confirm", type=int, default=2)
    r.add_argument("--seed", type=int, default=7)
    sub.add_parser("sweep", help="compare debounce settings and GSM conditions")
    sub.add_parser("rules", help="print alert thresholds and their sources")
    a = p.parse_args(argv)

    if a.cmd == "rules":
        print("| Vital | Critical < | Warning < | Warning > | Critical > | Source |\n|---|---|---|---|---|---|")
        for v in RULES.values():
            print(f"| {v.vital} ({v.unit}) | {v.critical_low} | {v.warning_low} | {v.warning_high} | "
                  f"{v.critical_high} | {v.source} |")
    elif a.cmd == "run":
        cfg = Config(patients=a.patients, duration_min=int(a.hours * 60), confirm_n=a.confirm, seed=a.seed)
        print(_fmt(run(cfg).score()))
    else:
        base = Config()
        cases = {
            "confirm_n=1 (no debounce)": replace(base, confirm_n=1),
            "confirm_n=2 (default)": base,
            "confirm_n=3": replace(base, confirm_n=3),
            "no GSM outage, no loss": replace(base, gsm_outages_min=(), loss_prob=0.0),
            "20% upload loss": replace(base, loss_prob=0.2),
            "2 h GSM outage (600-720)": replace(base, gsm_outages_min=((600, 720),)),
        }
        for name, cfg in cases.items():
            print(f"{name:28s} {_fmt(run(cfg).score())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
