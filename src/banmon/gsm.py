"""GSM link: an AT-command modem driver over a pluggable serial transport.

Chapter 5.3: a GSM module on the Raspberry Pi, driven by standard AT commands, carries data to the
cloud server and SMS alerts to doctors and relatives. ``Sim800Simulator`` emulates the subset of
SIM800-class AT commands used here, including coverage outages and lost uplink packets.
``PySerialTransport`` is the drop-in for a real modem on /dev/serial0. It is untested in this
repository because no modem is attached.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Protocol

CTRL_Z = "\x1a"


class Transport(Protocol):
    def write(self, data: str) -> None: ...
    def read_response(self) -> str: ...


@dataclass
class Sim800Simulator:
    """Answers AT commands the way a SIM800 does (subset). Time is driven by ``now_min``."""
    rng: random.Random
    loss_prob: float = 0.01  # per HTTP upload
    outages_min: tuple[tuple[int, int], ...] = ()
    now_min: int = 0
    sms_outbox: list[tuple[int, str, str]] = field(default_factory=list)  # (t, number, text)
    http_posted: list[tuple[int, str]] = field(default_factory=list)
    _pending: str = ""
    _sms_to: str | None = None
    _http_len: int | None = None
    _http_body: str = ""
    _mr: int = 0
    _text_mode: bool = False

    def in_coverage(self) -> bool:
        return not any(s <= self.now_min < e for s, e in self.outages_min)

    def write(self, data: str) -> None:
        self._pending = self._handle(data)

    def read_response(self) -> str:
        r, self._pending = self._pending, ""
        return r

    def _handle(self, data: str) -> str:
        if self._sms_to is not None:  # message body terminated by Ctrl-Z
            body, self._sms_to, to = data.rstrip(CTRL_Z), None, self._sms_to
            if not self.in_coverage():
                return "+CMS ERROR: 331"  # no network service
            self._mr += 1
            self.sms_outbox.append((self.now_min, to, body))
            return f"+CMGS: {self._mr}\r\nOK"
        if self._http_len is not None:
            self._http_body, self._http_len = data[: self._http_len], None
            return "OK"
        cmd = data.strip()
        if cmd == "AT":
            return "OK"
        if cmd == "AT+CSQ":
            return f"+CSQ: {18 if self.in_coverage() else 99},0\r\nOK"
        if cmd == "AT+CMGF=1":
            self._text_mode = True
            return "OK"
        if cmd.startswith('AT+CMGS="') and self._text_mode:
            self._sms_to = cmd[len('AT+CMGS="'):-1]
            return "> "
        if cmd in ("AT+HTTPINIT", "AT+HTTPTERM") or cmd.startswith('AT+HTTPPARA='):
            return "OK"
        if cmd.startswith("AT+HTTPDATA="):
            self._http_len = int(cmd.split("=")[1].split(",")[0])
            return "DOWNLOAD"
        if cmd == "AT+HTTPACTION=1":
            if not self.in_coverage():
                return "OK\r\n+HTTPACTION: 1,601,0"  # network error
            if self.rng.random() < self.loss_prob:
                return "OK\r\n+HTTPACTION: 1,408,0"  # timeout
            self.http_posted.append((self.now_min, self._http_body))
            return "OK\r\n+HTTPACTION: 1,200,2"
        return "ERROR"


class PySerialTransport:  # pragma: no cover - needs real hardware
    def __init__(self, port: str = "/dev/serial0", baud: int = 9600):
        import serial  # pyserial
        self._s = serial.Serial(port, baud, timeout=5)

    def write(self, data: str) -> None:
        self._s.write(data.encode() + (b"" if data.endswith(CTRL_Z) else b"\r\n"))

    def read_response(self) -> str:
        return self._s.read_until(b"OK\r\n").decode(errors="replace").strip()


class Modem:
    """AT driver used by the gateway. Every method returns True on success."""

    def __init__(self, transport: Transport, url: str = "http://cloud.example/ingest"):
        self.t = transport
        self.url = url

    def _at(self, cmd: str) -> str:
        self.t.write(cmd)
        return self.t.read_response()

    def ready(self) -> bool:
        return self._at("AT").endswith("OK") and not self._at("AT+CSQ").startswith("+CSQ: 99")

    def send_sms(self, number: str, text: str) -> bool:
        if not self._at("AT+CMGF=1").endswith("OK"):
            return False
        if not self._at(f'AT+CMGS="{number}"').startswith(">"):
            return False
        return "+CMGS:" in self._at(text[:160] + CTRL_Z)

    def http_post_json(self, payload: object) -> bool:
        body = json.dumps(payload, separators=(",", ":"))
        self._at("AT+HTTPINIT")
        self._at(f'AT+HTTPPARA="URL","{self.url}"')
        if self._at(f"AT+HTTPDATA={len(body)},10000") != "DOWNLOAD":
            return False
        self._at(body)
        ok = "+HTTPACTION: 1,200" in self._at("AT+HTTPACTION=1")
        self._at("AT+HTTPTERM")
        return ok
