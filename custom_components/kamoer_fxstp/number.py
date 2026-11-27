from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode

from .entity import FxStpEntity


async def async_setup_entry(hass, entry, add):
    add([FlowNumber(entry.runtime_data, "flow_set", "Flow rate", entry.title)])


class FlowNumber(FxStpEntity, NumberEntity):
    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = "mL/min"
    _attr_native_min_value = 1.0
    _attr_native_max_value = 150.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self):
        return self.srv.state.flow_ml_min

    async def async_set_native_value(self, value: float) -> None:
        await self.srv.set_flow(value)
