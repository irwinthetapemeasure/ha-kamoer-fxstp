from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import FxStpEntity


async def async_setup_entry(hass, entry, add):
    add([Connected(entry.runtime_data, "connected", "Connected", entry.title)])


class Connected(FxStpEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self):
        return self.srv.state.connected
