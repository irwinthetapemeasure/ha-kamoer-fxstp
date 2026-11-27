# Kamoer FX-STP protocol (captured November 2026, firmware FX_STP_EN-1.1.08)

Same transport as the X2SR: ESP8266, Alibaba Link Kit (sdk-c-2.3.0_FY_1.6.0-1),
MQTT over TLS 1.2 (AES256-SHA256) on :1883 to
`public.iot-as-mqtt.ap-southeast-1.aliyuncs.com`. No server-cert check.

Cold boot (same set as X2SR, all replayable): deviceinfo/update, ota inform,
thing/lan/prefix/get, /ext/ntp request, awss/enrollee/match, log/post.
Device SUBSCRIBEs only `/sys/<PK>/<DN>/thing/event/+/post_reply` (server-side
subs for commands, so a local proxy must inject the `thing/service/#` subscribe).

## Frames (hex in params.PumpSerialNumber)
`SS` = sequence byte, +1 per command (app wraps 0x20,0x21,...).

| Purpose | App → pump (`thing/service/property/set`) | Pump reply (`event/property/post`) |
|---|---|---|
| read status | `SS 00 0001 0002 0009 00` | `SS 00 0015 0052 0009` + 21 B status |
| start | `SS 02 000a 0002 0003 0001 0000000000000000` | `SS 02 0003 0052 0003 000000` |
| stop | `SS 02 000a 0002 0003 0000 0000000000000000` | `SS 02 0003 0052 0003 000000` |
| set flow | `SS 02 0006 0002 0002 0001 <float32 BE>` | `SS 02 0003 0052 0002 000000` |

Status body (after `SS 00 0015 0052 0009`):
| bytes | example | meaning |
|---|---|---|
| u32 | 0 / 1 | running |
| f32 | 0.400 | calibration, mL per revolution |
| f32 | 100.833 → 56.389 → 157.5 | motor RPM (flow mL/min = RPM × mL/rev; confirmed at the pump: 56.4 RPM = 22.6, 157.5 = 63) |
| u32 | 3600000 | ? constant (1 h in ms? limit?) |
| u32 | 2694213 → 2694285 | lifetime run seconds (+72 = 19 s + 53 s of running) |
| u8 | 00 | ? |

Flow: user set 63 → pump got 64.555; 22 → 22.555 (×~1.025 both times; app-side
calibration factor). Test sequence 17:15:07 start, 17:15:26 stop, 17:15:42 start,
17:15:56 flow 63, 17:16:12 flow 22, 17:16:35 stop.
