from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .fxstp import FxStpServer, PumpState


class FxStpEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, srv: FxStpServer, key: str, name: str, device_name: str) -> None:
        self.srv = srv
        self._attr_unique_id = f"{DOMAIN}_{srv.dn}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, srv.dn)}, name=device_name,
            manufacturer="Kamoer", model="FX-STP", sw_version=srv.state.firmware)

    @property
    def available(self) -> bool:
        return self.srv.state.connected and self.srv.state.running is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.srv.add_listener(self._on_state))

    @callback
    def _on_state(self, _s: PumpState) -> None:
        self.async_write_ha_state()
