from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory

from .entity import FxStpEntity


async def async_setup_entry(hass, entry, add):
    s, dn = entry.runtime_data, entry.title
    add([
        Sensor(s, dn, "flow", "Flow", "mL/min", "mdi:water", lambda st: st.flow_ml_min),
        Sensor(s, dn, "rpm", "Motor speed", "rpm", "mdi:rotate-right", lambda st: st.rpm),
        Sensor(s, dn, "runtime", "Total run time", "h", "mdi:timer-outline",
               lambda st: None if st.runtime_s is None else round(st.runtime_s / 3600, 2),
               SensorStateClass.TOTAL_INCREASING),
        Sensor(s, dn, "ml_per_rev", "Calibration", "mL/rev", "mdi:tune", lambda st: st.ml_per_rev,
               category=EntityCategory.DIAGNOSTIC),
    ])


class Sensor(FxStpEntity, SensorEntity):
    def __init__(self, srv, device_name, key, name, unit, icon, fn,
                 state_class=SensorStateClass.MEASUREMENT, category=None):
        super().__init__(srv, key, name, device_name)
        self._fn = fn
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_state_class = state_class
        self._attr_entity_category = category

    @property
    def native_value(self):
        return self._fn(self.srv.state)
