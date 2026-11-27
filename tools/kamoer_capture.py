#!/usr/bin/env python3
# Kamoer capture relay: learn how a Kamoer Wi-Fi pump talks to its cloud.
#
# Meant for a DNS rewrite: the LAN's DNS points the pump's cloud MQTT hostname at
# this host, so the pump connects HERE on LISTEN_PORT. Nothing else on the router
# is needed: no DNAT, no SSH.
#
#   pump --(TLS or plain MQTT)--> this relay --(same kind)--> real vendor cloud
#
# Per connection:
#   * peek the first byte: 0x16 = TLS ClientHello, 0x10 = plaintext MQTT CONNECT;
#   * TLS: terminate with a self-signed RSA cert (Kamoer's ESP firmware does not
#     check it) and note the SNI;
#   * upstream host = SNI, else UPSTREAM_HOST. It is resolved through a PUBLIC
#     resolver (not the LAN DNS, which now points back here) unless UPSTREAM_IP is set;
#   * relay bytes verbatim both ways and log every MQTT packet (CONNECT login,
#     topics, payloads) to the add-on log and CAPTURE_LOG.
# The pump keeps working through the real cloud the whole time; the app still works.
import os, socket, ssl, struct, threading, time, random

LISTEN_PORT   = int(os.environ.get("LISTEN_PORT", "1883"))
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "").strip()
UPSTREAM_IP   = os.environ.get("UPSTREAM_IP", "").strip()
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "1883"))
RESOLVER      = os.environ.get("RESOLVER", "1.1.1.1")
CERT = os.environ.get("CERT", "fake.crt")
KEY  = os.environ.get("KEY", "fake.key")
LOG  = os.environ.get("CAPTURE_LOG", "capture.log")

os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
_logf = open(LOG, "a", buffering=1)
_loglock = threading.Lock()

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with _loglock:
        _logf.write(line + "\n")
        print(line, flush=True)

# ---------- public DNS lookup (bypasses the LAN rewrite) ----------
def _skip_name(buf, i):
    while True:
        n = buf[i]
        if n == 0: return i + 1
        if n & 0xC0 == 0xC0: return i + 2
        i += n + 1

def resolve_public(host):
    qid = random.randint(0, 0xFFFF)
    q = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in host.rstrip(".").split(".")) + b"\0"
    q += struct.pack("!HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
    try:
        s.sendto(q, (RESOLVER, 53)); buf = s.recv(2048)
    finally:
        s.close()
    rid, flags, qd, an = struct.unpack("!HHHH", buf[:8])
    if rid != qid or flags & 0xF: raise RuntimeError(f"DNS error rcode={flags & 0xF}")
    i = 12
    for _ in range(qd): i = _skip_name(buf, i) + 4
    for _ in range(an):
        i = _skip_name(buf, i)
        typ, _cls, _ttl, rdlen = struct.unpack("!HHIH", buf[i:i + 10]); i += 10
        if typ == 1 and rdlen == 4: return socket.inet_ntoa(buf[i:i + 4])
        i += rdlen
    raise RuntimeError("no A record")

# ---------- MQTT decode ----------
TYPES = {1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC", 6: "PUBREL",
         7: "PUBCOMP", 8: "SUBSCRIBE", 9: "SUBACK", 10: "UNSUBSCRIBE", 11: "UNSUBACK",
         12: "PINGREQ", 13: "PINGRESP", 14: "DISCONNECT"}

def iter_packets(buf):
    i, n, out = 0, len(buf), []
    while i < n:
        b0, mult, val, j = buf[i], 1, 0, i + 1
        while True:
            if j >= n: return out, buf[i:]
            d = buf[j]; val += (d & 0x7F) * mult; j += 1
            if not d & 0x80: break
            mult *= 128
        end = j + val
        if end > n: return out, buf[i:]
        out.append((b0, buf[j:end])); i = end
    return out, b""

def _mqtt_str(b, i):
    n = (b[i] << 8) | b[i + 1]
    return b[i + 2:i + 2 + n].decode("utf-8", "replace"), i + 2 + n

def decode_connect(body):
    proto, i = _mqtt_str(body, 0)
    level, flags = body[i], body[i + 1]; i += 4          # level, flags, keepalive(2)
    if level == 5:                                          # skip v5 properties
        plen, mult = 0, 1
        while True:
            d = body[i]; i += 1; plen += (d & 0x7F) * mult; mult *= 128
            if not d & 0x80: break
        i += plen
    cid, i = _mqtt_str(body, i)
    parts = [f"proto={proto} v{level} clientId={cid}"]
    if flags & 0x04:                                        # will
        wt, i = _mqtt_str(body, i); _wm, i = _mqtt_str(body, i); parts.append(f"will={wt}")
    if flags & 0x80: u, i = _mqtt_str(body, i); parts.append(f"username={u}")
    if flags & 0x40: p, i = _mqtt_str(body, i); parts.append(f"password={p}")
    return " ".join(parts)

def decode(direction, b0, body):
    typ = b0 >> 4
    if typ == 3:
        qos = (b0 >> 1) & 3
        topic, off = _mqtt_str(body, 0)
        pid = ""
        if qos:
            pid = f" pid={(body[off] << 8) | body[off + 1]}"; off += 2
        payload = body[off:]
        try: ptxt = payload.decode("utf-8")
        except UnicodeDecodeError: ptxt = "hex:" + payload.hex()
        log(f"{direction} PUBLISH qos={qos}{pid} topic={topic}")
        log(f"{direction}     payload={ptxt}")
    elif typ == 1:
        try: log(f"{direction} CONNECT {decode_connect(body)}")
        except Exception: log(f"{direction} CONNECT hex={body.hex()}")
    elif typ in (8, 10):
        log(f"{direction} {TYPES[typ]} hex={body.hex()} ascii={body.decode('latin-1')!r}")
    else:
        log(f"{direction} {TYPES.get(typ, f'?{typ}')} ({len(body)}B)"
            + (f" hex={body.hex()}" if len(body) < 24 else ""))

def relay(src, dst, direction):
    buf = b""
    try:
        while True:
            data = src.recv(4096)
            if not data: break
            dst.sendall(data)
            buf += data
            pkts, buf = iter_packets(buf)
            for b0, body in pkts:
                try: decode(direction, b0, body)
                except Exception as e: log(f"{direction} decode-err {e} hex={body.hex()[:120]}")
    except Exception as e:
        log(f"{direction} ended: {e}")
    finally:
        for s in (src, dst):
            try: s.shutdown(socket.SHUT_RDWR)
            except Exception: pass

def server_ctx():
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    c.load_cert_chain(CERT, KEY)
    try: c.minimum_version = ssl.TLSVersion.TLSv1
    except Exception: pass
    try: c.set_ciphers("ALL:@SECLEVEL=0")
    except Exception: pass
    return c

def client_ctx():
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False; c.verify_mode = ssl.CERT_NONE
    try: c.set_ciphers("ALL:@SECLEVEL=0")
    except Exception: pass
    return c

def handle(raw, addr):
    sni = {"name": None}
    try:
        raw.settimeout(20)
        first = raw.recv(1, socket.MSG_PEEK)
        if not first:
            raw.close(); return
        is_tls = first[0] == 0x16
        log(f"=== {addr[0]}:{addr[1]} connected, {'TLS' if is_tls else 'plaintext'} (first byte 0x{first[0]:02x}) ===")
        dev = raw
        if is_tls:
            sctx = server_ctx()
            sctx.sni_callback = lambda s, name, c: sni.__setitem__("name", name)
            dev = sctx.wrap_socket(raw, server_side=True)
            log(f"device TLS up: SNI={sni['name']!r} {dev.version()} cipher={dev.cipher()[0]}")
        dev.settimeout(None)
        host = sni["name"] or UPSTREAM_HOST
        if not (host or UPSTREAM_IP):
            log("no SNI and no upstream_host/upstream_ip set: can't relay, logging the first packet only")
            data = dev.recv(4096)
            for b0, body in iter_packets(data)[0]: decode("DEV->?", b0, body)
            dev.close(); return
        ip = UPSTREAM_IP or resolve_public(host)
        up = socket.create_connection((ip, UPSTREAM_PORT), timeout=15)
        if is_tls:
            up = client_ctx().wrap_socket(up, server_hostname=host or None)
        up.settimeout(None)
        log(f"upstream up: {host or '-'} -> {ip}:{UPSTREAM_PORT}")
    except Exception as e:
        log(f"setup failed for {addr}: {e!r}")
        try: raw.close()
        except Exception: pass
        return
    t1 = threading.Thread(target=relay, args=(dev, up, "DEV->CLOUD"), daemon=True)
    t2 = threading.Thread(target=relay, args=(up, dev, "CLOUD->DEV"), daemon=True)
    t1.start(); t2.start(); t1.join(); t2.join()
    log(f"=== {addr[0]}:{addr[1]} closed ===")

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN_PORT)); srv.listen(8)
    log(f"Kamoer capture relay on :{LISTEN_PORT} (upstream_host={UPSTREAM_HOST or 'from SNI'}, "
        f"port {UPSTREAM_PORT}); log {LOG}")
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()

if __name__ == "__main__":
    main()
