"""Kamoer FX-STP local server: stands in for the Kamoer/Alibaba cloud.

The pump (ESP8266, Alibaba Link Kit) connects with MQTT over TLS to whatever its
cloud hostname resolves to (we point it at this host with a DNS record). This
module IS the broker for that one connection: it answers the pump's boot
requests, polls status, and sends start/stop/flow frames. No Kamoer cloud.

Pure asyncio + stdlib, so it runs inside Home Assistant or standalone.
Protocol notes: PROTOCOL-FXSTP.md in the repo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

_LOG = logging.getLogger(__name__)

# ---------------- MQTT bits ----------------
def _enc_len(n: int) -> bytes:
    out = bytearray()
    while True:
        d, n = n % 128, n // 128
        out.append(d | (0x80 if n else 0))
        if not n:
            return bytes(out)

def _pkt(b0: int, body: bytes) -> bytes:
    return bytes([b0]) + _enc_len(len(body)) + body

def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b

def _publish(topic: str, payload: str) -> bytes:
    return _pkt(0x30, _str(topic) + payload.encode())

def _iter_packets(buf: bytes):
    i, n, out = 0, len(buf), []
    while i < n:
        b0, mult, val, j = buf[i], 1, 0, i + 1
        while True:
            if j >= n:
                return out, buf[i:]
            d = buf[j]; val += (d & 0x7F) * mult; j += 1
            if not d & 0x80:
                break
            mult *= 128
        end = j + val
        if end > n:
            return out, buf[i:]
        out.append((b0, buf[j:end])); i = end
    return out, b""

def _echo_id(payload: str) -> str:
    try:
        return str(json.loads(payload).get("id", "0"))
    except Exception:
        return "0"


@dataclass
class PumpState:
    connected: bool = False
    running: bool | None = None
    ml_per_rev: float | None = None
    rpm: float | None = None
    flow_ml_min: float | None = None      # rpm * ml_per_rev
    runtime_s: int | None = None          # lifetime running seconds
    const_c: int | None = None            # unknown constant (3600000 seen)
    firmware: str | None = None
    last_seen: float | None = None
    raw_status: str | None = None
    extra: dict = field(default_factory=dict)


class FxStpServer:
    """One pump, one TLS listener."""

    def __init__(self, product_key: str, device_name: str, device_secret: str,
                 lan_prefix: str, certfile: str, keyfile: str,
                 host: str = "0.0.0.0", port: int = 1883, poll_s: float = 15.0,
                 mqtt_username: str | None = None, mqtt_password: str | None = None):
        self.pk, self.dn = product_key, device_name
        self.base = f"/sys/{product_key}/{device_name}"
        self.lan = {"deviceSecret": device_secret, "prefix": lan_prefix,
                    "productKey": product_key, "deviceName": device_name}
        self.certfile, self.keyfile = certfile, keyfile
        self.host, self.port, self.poll_s = host, port, poll_s
        self.mqtt_username, self.mqtt_password = mqtt_username, mqtt_password
        self.state = PumpState()
        self._listeners: list[Callable[[PumpState], None]] = []
        self._writer: asyncio.StreamWriter | None = None
        self._wlock = asyncio.Lock()
        self._seq = random.randint(0x40, 0xBF)
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---------- public API ----------
    def add_listener(self, cb: Callable[[PumpState], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def make_ssl_context(self) -> ssl.SSLContext:
        """Blocking (reads the cert); run in an executor inside Home Assistant."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.certfile, self.keyfile)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except Exception:
            pass
        try:
            ctx.set_ciphers("ALL:@SECLEVEL=0")   # ESP8266 offers AES256-SHA256 only
        except Exception:
            pass
        return ctx

    async def start(self, ctx: ssl.SSLContext | None = None) -> None:
        ctx = ctx or self.make_ssl_context()
        self._server = await asyncio.start_server(self._handle, self.host, self.port, ssl=ctx)
        self._spawn(self._poll_loop())
        _LOG.info("FX-STP local server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._writer:
            self._writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def pump_start(self) -> None:
        await self._cmd("02000a000200030001" + "00" * 8)
        await self._delayed_poll()

    async def pump_stop(self) -> None:
        await self._cmd("02000a000200030000" + "00" * 8)
        await self._delayed_poll()

    async def set_flow(self, ml_min: float) -> None:
        await self._cmd("020006000200020001" + struct.pack(">f", float(ml_min)).hex())
        await self._delayed_poll()

    async def poll(self) -> None:
        await self._cmd("0000010002000900")

    # ---------- internals ----------
    def _spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self.state)
            except Exception:
                _LOG.exception("listener failed")

    async def _send(self, data: bytes) -> None:
        w = self._writer
        if w is None:
            raise ConnectionError("pump not connected")
        async with self._wlock:
            w.write(data)
            await w.drain()

    async def _cmd(self, body_hex: str) -> None:
        self._seq = (self._seq + 1) & 0xFF
        payload = json.dumps({"method": "thing.service.property.set",
                              "id": str(int(time.time() * 1000) % 2_000_000_000),
                              "params": {"PumpSerialNumber": f"{self._seq:02x}{body_hex}"},
                              "version": "1.0.0"})
        _LOG.debug("-> pump %s", payload)
        await self._send(_publish(f"{self.base}/thing/service/property/set", payload))

    async def _delayed_poll(self) -> None:
        async def later():
            await asyncio.sleep(2)
            try:
                await self.poll()
            except ConnectionError:
                pass
        self._spawn(later())

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            if self._writer is not None:
                try:
                    await self.poll()
                except Exception as e:
                    _LOG.debug("poll failed: %s", e)

    def _boot_reply(self, topic: str, payload: str) -> bytes | None:
        def ok(method: str) -> str:
            return json.dumps({"code": 200, "data": {}, "id": _echo_id(payload),
                               "message": "success", "method": method, "version": "1.0"})
        b = self.base
        if topic == f"{b}/thing/lan/prefix/get":
            return _publish(f"{b}/thing/lan/prefix/get_reply", json.dumps(
                {"code": 200, "data": self.lan, "id": _echo_id(payload), "message": "success",
                 "method": "thing.lan.prefix.get", "version": "1.0"}))
        if topic == f"{b}/thing/deviceinfo/update":
            return _publish(f"{b}/thing/deviceinfo/update_reply", ok("thing.deviceinfo.update"))
        if topic == f"{b}/thing/awss/enrollee/match":
            return _publish(f"{b}/thing/awss/enrollee/match_reply", ok("thing.awss.enrollee.match"))
        if topic == f"{b}/thing/event/property/post":
            return _publish(f"{b}/thing/event/property/post_reply", ok("thing.event.property.post"))
        if topic == f"/ext/ntp/{self.pk}/{self.dn}/request":
            try:
                dst = json.loads(payload).get("deviceSendTime", "0")
            except Exception:
                dst = "0"
            now = str(int(time.time() * 1000))
            return _publish(f"/ext/ntp/{self.pk}/{self.dn}/response", json.dumps(
                {"deviceSendTime": dst, "serverSendTime": now, "serverRecvTime": now}))
        return None

    def _on_frame(self, h: str) -> None:
        h = h.lower()
        # status: SS 00 0015 0052 0009 | u32 run | f32 ml/rev | f32 rpm | u32 c | u32 runtime | u8
        # e.g. c0 00 0015 0052 0009 00000000 3ecccccd 431d8000 0036ee80 00291c8d 00
        if len(h) >= 56 and h[2:16] == "00001500520009":
            b = bytes.fromhex(h[16:])
            run, mlrev, rpm, c, rt = struct.unpack(">IffII", b[:20])
            s = self.state
            s.running, s.ml_per_rev, s.rpm = bool(run), round(mlrev, 4), round(rpm, 3)
            s.flow_ml_min = round(rpm * mlrev, 2)
            s.const_c, s.runtime_s, s.raw_status = c, rt, h
            self._notify()
        elif h[2:4] == "02":
            _LOG.debug("pump ack %s", h)
        else:
            _LOG.debug("pump frame %s", h)

    def _on_publish(self, topic: str, payload: str) -> None:
        self.state.last_seen = time.time()
        if "/ota/device/inform/" in topic:
            try:
                self.state.firmware = json.loads(payload)["params"]["version"]
            except Exception:
                pass
        if topic.endswith("/thing/event/property/post"):
            try:
                raw = (json.loads(payload).get("params") or {}).get("PumpSerialNumber", "")
            except Exception:
                raw = ""
            if raw:
                self._on_frame(raw)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        _LOG.info("pump connected from %s", peer)
        if self._writer is not None:          # newest connection wins
            try:
                self._writer.close()
            except Exception:
                pass
        self._writer = writer
        buf = b""
        try:
            while data := await reader.read(4096):
                buf += data
                pkts, buf = _iter_packets(buf)
                for b0, body in pkts:
                    await self._on_packet(b0, body)
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError) as e:
            _LOG.info("pump connection ended: %s", e)
        finally:
            if self._writer is writer:
                self._writer = None
                self.state.connected = False
                self._notify()
            try:
                writer.close()
            except Exception:
                pass
            _LOG.info("pump disconnected %s", peer)

    async def _on_packet(self, b0: int, body: bytes) -> None:
        typ = b0 >> 4
        if typ == 1:                                   # CONNECT
            rc = 0
            if self.mqtt_username:
                try:
                    i = 2 + ((body[0] << 8) | body[1]) + 4
                    cl = (body[i] << 8) | body[i + 1]; i += 2 + cl
                    ul = (body[i] << 8) | body[i + 1]; user = body[i + 2:i + 2 + ul].decode()
                    if user != self.mqtt_username:
                        rc = 4
                except Exception:
                    pass
            await self._send(_pkt(0x20, bytes([0, rc])))
            if rc:
                _LOG.warning("refused CONNECT from unexpected device")
                raise ConnectionError("bad login")
            self.state.connected = True
            self._notify()
            self._spawn(self._first_poll())
        elif typ == 3:                                 # PUBLISH
            qos = (b0 >> 1) & 3
            tl = (body[0] << 8) | body[1]
            topic = body[2:2 + tl].decode("utf-8", "replace"); off = 2 + tl
            if qos:
                pid = body[off:off + 2]; off += 2
                await self._send(_pkt(0x40, pid))     # PUBACK
            payload = body[off:].decode("utf-8", "replace")
            rep = self._boot_reply(topic, payload)
            if rep:
                await self._send(rep)
            self._on_publish(topic, payload)
        elif typ == 8:                                 # SUBSCRIBE -> grant all at qos0
            pid, i, n = body[:2], 2, 0
            while i < len(body):
                tl = (body[i] << 8) | body[i + 1]; i += 2 + tl + 1; n += 1
            await self._send(_pkt(0x90, pid + b"\x00" * n))
        elif typ == 12:                                # PINGREQ
            await self._send(b"\xd0\x00")
        elif typ == 14:                                # DISCONNECT
            raise ConnectionError("pump sent DISCONNECT")

    async def _first_poll(self) -> None:
        await asyncio.sleep(3)
        try:
            await self.poll()
        except ConnectionError:
            pass
