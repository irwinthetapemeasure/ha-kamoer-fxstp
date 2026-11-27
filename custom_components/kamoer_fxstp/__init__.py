"""Kamoer FX-STP: fully local control (Home Assistant stands in for Kamoer's cloud).

configuration.yaml (imported into a config entry on start):

kamoer_fxstp:
  product_key: !secret kamoer_product_key
  device_name: !secret kamoer_device_name
  device_secret: !secret kamoer_device_secret
  lan_prefix: !secret kamoer_lan_prefix
  mqtt_username: !secret kamoer_mqtt_username   # optional: refuse other devices
  port: 1883                                    # pump dials 1883 (TLS)
  name: Kamoer FX-STP                           # optional device name

The pump must reach this host: a local DNS record for its cloud MQTT hostname
(e.g. public.iot-as-mqtt.<region>.aliyuncs.com) pointing at Home Assistant.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import DEFAULT_NAME, DOMAIN
from .fxstp import FxStpServer

_LOG = logging.getLogger(__name__)
PLATFORMS = [Platform.SWITCH, Platform.NUMBER, Platform.SENSOR, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({
    vol.Required("product_key"): cv.string,
    vol.Required("device_name"): cv.string,
    vol.Required("device_secret"): cv.string,
    vol.Required("lan_prefix"): cv.string,
    vol.Optional("mqtt_username"): cv.string,
    vol.Optional("port", default=1883): cv.port,
    vol.Optional("poll_seconds", default=15): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
    vol.Optional("name", default=DEFAULT_NAME): cv.string,
})}, extra=vol.ALLOW_EXTRA)


RENEW_BEFORE = dt.timedelta(days=365)      # renew when less than a year is left
CHECK_EVERY = dt.timedelta(days=1)


def _cert_expiry(cert: str) -> dt.datetime | None:
    from cryptography import x509
    try:
        with open(cert, "rb") as f:
            return x509.load_pem_x509_certificate(f.read()).not_valid_after_utc
    except Exception:
        return None


def _ensure_cert(cert: str, key: str) -> bool:
    """Make the cert if missing, unreadable or within RENEW_BEFORE of expiry.

    Returns True if a new cert was written (old one kept as *.old)."""
    exp = _cert_expiry(cert) if os.path.exists(key) else None
    if exp is not None and exp - dt.datetime.now(dt.timezone.utc) > RENEW_BEFORE:
        return False
    for p in (cert, key):
        if os.path.exists(p):
            os.replace(p, p + ".old")
    _make_cert(cert, key)
    _LOG.warning("Kamoer FX-STP: %s TLS cert (old expiry %s, new expiry %s)",
                 "renewed" if exp else "created", exp, _cert_expiry(cert))
    return True


def _make_cert(cert: str, key: str) -> None:
    """Self-signed RSA cert (the pump doesn't check it; ESP8266 needs RSA)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(os.path.dirname(cert), exist_ok=True)
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iot.example.com")])
    now = dt.datetime.now(dt.timezone.utc)
    # Same shape as `openssl req -x509` (SKI, AKI, basicConstraints CA). On 2026-11-26 the
    # real pump reset the TLS handshake on a bare cert with no extensions, and accepted this shape.
    ski = x509.SubjectKeyIdentifier.from_public_key(k.public_key())
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=3650))
         .add_extension(ski, critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
         .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
         .sign(k, hashes.SHA256()))
    with open(key, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()))
    os.chmod(key, 0o600)
    with open(cert, "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(config[DOMAIN])))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    c = entry.data
    d = hass.config.path(".storage", DOMAIN)
    cert, key = os.path.join(d, "server.crt"), os.path.join(d, "server.key")
    await hass.async_add_executor_job(_ensure_cert, cert, key)
    srv = FxStpServer(c["product_key"], c["device_name"], c["device_secret"], c["lan_prefix"],
                      cert, key, port=c.get("port", 1883), poll_s=c.get("poll_seconds", 15),
                      mqtt_username=c.get("mqtt_username"))
    ctx = await hass.async_add_executor_job(srv.make_ssl_context)
    await srv.start(ctx)
    entry.runtime_data = srv
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _daily_cert_check(_now) -> None:
        # Renewing = new cert + reload: the pump drops for ~10 s and keeps pumping meanwhile.
        if await hass.async_add_executor_job(_ensure_cert, cert, key):
            hass.config_entries.async_schedule_reload(entry.entry_id)
    entry.async_on_unload(async_track_time_interval(hass, _daily_cert_check, CHECK_EVERY))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.stop()
    return ok
