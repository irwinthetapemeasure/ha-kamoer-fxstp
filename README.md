# Kamoer FX-STP for Home Assistant: fully local, no cloud

A Home Assistant custom integration that controls a **Kamoer FX-STP** Wi-Fi stepper
(peristaltic) pump **with no Kamoer cloud and no phone app**. Home Assistant stands in for
Kamoer's Alibaba IoT cloud: the pump connects to HA, and HA answers its boot requests, reads its
status and sends commands.

> Built on the reverse-engineering of the Kamoer **X2SR** by **[@ffirenmatana](https://github.com/ffirenmatana)**.
> See [Credits](#credits). Not affiliated with or endorsed by Kamoer.

## What you get

| Entity | What it does |
|---|---|
| `switch.<name>_pump` | Run / stop the pump |
| `number.<name>_flow_rate` | Set flow (mL/min) |
| `sensor.<name>_flow` | Flow the pump reports (RPM × calibration) |
| `sensor.<name>_motor_speed` | Motor RPM |
| `sensor.<name>_total_run_time` | Lifetime running hours (from the pump) |
| `sensor.<name>_calibration` | mL per revolution (diagnostic) |
| `binary_sensor.<name>_connected` | Pump is connected to HA (diagnostic) |

Tested with firmware **FX_STP_EN-1.1.08** on Home Assistant **2026.10** and **2026.11**.

## How it works
- The FX-STP is an ESP8266 running Alibaba's Link Kit SDK. It speaks **MQTT over TLS on port
  1883** to `public.iot-as-mqtt.<region>.aliyuncs.com` and **does not verify the server
  certificate**.
- A **local DNS record** points that hostname at your Home Assistant host. The integration
  listens on 1883 with its own self-signed certificate, which it creates and auto-renews.
- At boot the pump asks the "cloud" for a few things (a LAN prefix + device secret, NTP time, some
  acks). The integration answers from the keys you captured once (step 1).
- All control rides in one thing-model property, `PumpSerialNumber`, as hex frames. See
  [docs/PROTOCOL-FXSTP.md](docs/PROTOCOL-FXSTP.md).

## Behaviour you should know about
- **The Kamoer app stops working** for this pump (it shows offline). To go back, see [Undo](#undo).
- **If Home Assistant goes down, the pump keeps running** at its last setting and reconnects about
  10 s after HA is back. (Measured: run counter kept counting through a ~60 s outage.)
- Not yet verified: whether the pump resumes running by itself after a mains power cut.
- The pump's own screen keeps working.
- Interoperability work on hardware you own. Use at your own risk, no warranty. If the pump
  feeds something that matters (fuel, dosing), add your own alerts in HA, e.g. when `connected`
  goes off or the pump stops unexpectedly.

## Requirements
- A DNS server on your LAN where you can add a host record (UniFi, Pi-hole, AdGuard, OpenWrt, etc.).
  Many routers also intercept DNS, which helps if the pump uses a hard-coded resolver.
- **Port 1883 free on the HA host.** If the Mosquitto add-on publishes 1883 on the host, change its
  host port (e.g. 1884), or set `port:` here and redirect 1883 on your router.
- The pump's keys (step 1).

## 1. Capture your pump's keys (once)
Each pump has its own ProductKey, DeviceName, device secret and LAN prefix. You capture them
once while the pump still talks to the real cloud, with [`tools/kamoer_capture.py`](tools/kamoer_capture.py).
It's a transparent relay: it passes everything through to Kamoer, so the app keeps working,
while it logs every MQTT message.

1. Find the pump's cloud hostname: your router's DNS log or client "flows" view. It's usually
   `public.iot-as-mqtt.<region>.aliyuncs.com` on port 1883.
2. On any Linux box on the LAN with Python 3 and port 1883 free (not your HA host if Mosquitto
   uses 1883):
   ```sh
   openssl req -x509 -newkey rsa:2048 -keyout fake.key -out fake.crt -days 30 -nodes -subj /CN=iot.example.com
   python3 tools/kamoer_capture.py          # listens on :1883, logs to capture.log
   ```
   (Env vars: `LISTEN_PORT`, `UPSTREAM_PORT`, `UPSTREAM_HOST`, `CERT`, `KEY`, `CAPTURE_LOG`.)
3. Add a DNS record: the pump's hostname → that box's IP.
4. **Power-cycle the pump.** In `capture.log` you'll find:
   - `CONNECT ... username=<DeviceName>&<ProductKey>`
   - `thing/lan/prefix/get_reply` with `deviceSecret`, `prefix`, `productKey`, `deviceName`
   - optional: use the app (start, stop, flow) to see the command frames.
5. Remove the DNS record (or repoint it at HA in step 3) and stop the relay.

Treat these values like a password: anyone with them can impersonate your pump.

## 2. Install
**HACS:** HACS → ⋮ → Custom repositories → add this repo's URL as an *Integration* → install
"Kamoer FX-STP (local)".
**Manual:** copy `custom_components/kamoer_fxstp` into `/config/custom_components/`.

## 3. Configure
`secrets.yaml`:
```yaml
kamoer_product_key: "..."
kamoer_device_name: "..."
kamoer_device_secret: "..."
kamoer_lan_prefix: "..."
kamoer_mqtt_username: "<DeviceName>&<ProductKey>"
```
`configuration.yaml`:
```yaml
kamoer_fxstp:
  name: Kamoer FX-STP            # optional; becomes the device/entity name
  product_key: !secret kamoer_product_key
  device_name: !secret kamoer_device_name
  device_secret: !secret kamoer_device_secret
  lan_prefix: !secret kamoer_lan_prefix
  mqtt_username: !secret kamoer_mqtt_username   # optional: refuse any other device
  port: 1883                     # optional
  poll_seconds: 15               # optional, 5-120
```
Restart Home Assistant. YAML is imported into a config entry (one pump per entry).

## 4. Point the pump at Home Assistant
Add a DNS record: `public.iot-as-mqtt.<region>.aliyuncs.com` → your HA host's IP. The pump
re-resolves the name whenever it reconnects. If it's mid-session on the cloud, power-cycle it
or wait for its connection to drop.

## Undo
Remove the DNS record and the `kamoer_fxstp:` block, then restart HA. The pump goes back to the cloud.

## Other Kamoer models
Many Kamoer Wi-Fi products use the same Alibaba Link Kit transport and the same `PumpSerialNumber`
tunnel, but the frames differ per model. [`tools/fxstp_standalone.py`](tools/fxstp_standalone.py)
runs the local server without HA for bench testing (control port `127.0.0.1:8884`). Captures
and PRs for other models are welcome.

## Credits
- **[@ffirenmatana](https://github.com/ffirenmatana)** for reverse-engineering the Kamoer X2SR:
  [kamoer](https://github.com/ffirenmatana/kamoer) (write-up and reference code) and
  [kamoer-x2sr-addon](https://github.com/ffirenmatana/kamoer-x2sr-addon). Their findings made this
  possible: that the TLS certificate isn't checked, that the MQTT login is static, the
  `PumpSerialNumber` tunnel, the cold-boot handshake a local "cloud" has to answer, and
  server-side subscriptions. This project's boot replies follow their proxy closely.
- This repo adds the FX-STP frame decode, runs the "cloud" inside Home Assistant as a native
  integration (no separate broker or add-on), and auto-renews the TLS certificate.

## License
MIT for the code in this repository; see [LICENSE](LICENSE). The upstream X2SR repositories are
credited above and are covered by their own terms.
