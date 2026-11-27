"""Config entry comes from configuration.yaml (import); no UI setup (keys are captured, not typed)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DEFAULT_NAME, DOMAIN


class FxStpConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, data: dict):
        await self.async_set_unique_id(data["device_name"])
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=data.get("name", DEFAULT_NAME), data=data)

    async def async_step_user(self, user_input=None):
        return self.async_abort(reason="yaml_only")
