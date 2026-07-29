"""Config flow for BYD SH6K Passive Modbus integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEBUG_SENSOR,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_PUBLISH_INTERVAL,
    CONF_RECONNECT_DELAY,
    DEFAULT_DEBUG_SENSOR,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PUBLISH_INTERVAL,
    DEFAULT_RECONNECT_DELAY,
    DOMAIN,
)


class BydSh6kPassiveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BYD SH6K Passive Modbus."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(CONF_PUBLISH_INTERVAL, default=DEFAULT_PUBLISH_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                vol.Required(CONF_RECONNECT_DELAY, default=DEFAULT_RECONNECT_DELAY): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                vol.Required(CONF_DEBUG_SENSOR, default=DEFAULT_DEBUG_SENSOR): bool,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return BydSh6kPassiveOptionsFlow(config_entry)


class BydSh6kPassiveOptionsFlow(config_entries.OptionsFlow):
    """Options flow for BYD SH6K Passive Modbus."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = {**self._config_entry.data, **self._config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=data.get(CONF_HOST, DEFAULT_HOST)): str,
                vol.Required(CONF_PORT, default=data.get(CONF_PORT, DEFAULT_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(CONF_PUBLISH_INTERVAL, default=data.get(CONF_PUBLISH_INTERVAL, DEFAULT_PUBLISH_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                vol.Required(CONF_RECONNECT_DELAY, default=data.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY)): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                vol.Required(CONF_DEBUG_SENSOR, default=data.get(CONF_DEBUG_SENSOR, DEFAULT_DEBUG_SENSOR)): bool,
            }),
        )
