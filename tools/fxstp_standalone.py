#!/usr/bin/env python3
"""Run the FX-STP local server without Home Assistant (bench testing).

  python3 fxstp_standalone.py <device.json> <cert> <key> [port]

Control port 127.0.0.1:8884, one command per line:
  status | start | stop | flow <ml_min> | poll
"""
import asyncio, json, logging, sys, os, dataclasses
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "kamoer_fxstp"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fxstp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

async def main():
    d = json.load(open(sys.argv[1]))
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 1883
    srv = fxstp.FxStpServer(d["productKey"], d["deviceName"], d["deviceSecret"], d["prefix"],
                            sys.argv[2], sys.argv[3], port=port, poll_s=15,
                            mqtt_username=d.get("mqtt_username"))
    srv.add_listener(lambda s: logging.info("STATE %s", json.dumps(
        {k: v for k, v in dataclasses.asdict(s).items() if k not in ("raw_status", "extra")})))
    await srv.start()

    async def ctl(r, w):
        while line := (await r.readline()).decode().strip():
            try:
                a = line.split()
                if a[0] == "start": await srv.pump_start(); out = "ok"
                elif a[0] == "stop": await srv.pump_stop(); out = "ok"
                elif a[0] == "flow": await srv.set_flow(float(a[1])); out = "ok"
                elif a[0] == "poll": await srv.poll(); out = "ok"
                else: out = json.dumps(dataclasses.asdict(srv.state))
            except Exception as e:
                out = f"error {e!r}"
            logging.info("CTL %s -> %s", line, out[:200])
            w.write((out + "\n").encode()); await w.drain()
        w.close()
    await asyncio.start_server(ctl, "127.0.0.1", 8884)
    await asyncio.Event().wait()

asyncio.run(main())
