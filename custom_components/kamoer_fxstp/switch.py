from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .entity import FxStpEntity


async def async_setup_entry(hass, entry, add):
    add([PumpSwitch(entry.runtime_data, "run", "Pump", entry.title)])


class PumpSwitch(FxStpEntity, SwitchEntity):
    _attr_icon = "mdi:pump"

    @property
    def is_on(self):
        return self.srv.state.running

    async def async_turn_on(self, **kw):
        await self.srv.pump_start()

    async def async_turn_off(self, **kw):
        await self.srv.pump_stop()
