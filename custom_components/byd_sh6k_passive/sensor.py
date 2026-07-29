"""Sensors for BYD SH6K Passive Modbus."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NAME, DEFAULT_NAME, DOMAIN
from .parser import DEBUG_KEY, SENSOR_DESCRIPTIONS, BydPassiveClient

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up BYD SH6K sensors."""
    client: BydPassiveClient = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [BydSensor(client, entry, key, desc) for key, desc in SENSOR_DESCRIPTIONS.items()]
    if client.debug_enabled:
        entities.append(BydDebugSensor(client, entry))
    async_add_entities(entities)

class BydBaseSensor(SensorEntity):
    """Base BYD sensor."""

    _attr_has_entity_name = True

    def __init__(self, client: BydPassiveClient, entry: ConfigEntry) -> None:
        self.client = client
        self.entry = entry
        self._remove_listener = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data.get(CONF_NAME, DEFAULT_NAME),
            "manufacturer": "BYD",
            "model": "Power-Box SH6K",
        }

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.client.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.client.connected or self.native_value is not None

class BydSensor(BydBaseSensor):
    """BYD parsed sensor."""

    def __init__(self, client: BydPassiveClient, entry: ConfigEntry, key: str, desc: dict[str, Any]) -> None:
        super().__init__(client, entry)
        self.key = key
        self.desc = desc
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"BYD {desc['name']}"
        self._attr_native_unit_of_measurement = desc.get("unit")
        self._attr_device_class = desc.get("device_class")
        self._attr_state_class = desc.get("state_class")
        self._attr_icon = desc.get("icon")
        if desc.get("entity_category") == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        return self.client.states.get(self.key)

class BydDebugSensor(BydBaseSensor):
    """Diagnostic sensor with observed registers and raw data."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:code-json"

    def __init__(self, client: BydPassiveClient, entry: ConfigEntry) -> None:
        super().__init__(client, entry)
        self._attr_unique_id = f"{entry.entry_id}_{DEBUG_KEY}"
        self._attr_name = "BYD Debug Registri Osservati"

    @property
    def native_value(self) -> str:
        return "online" if self.client.connected else "offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.client.debug_state()
