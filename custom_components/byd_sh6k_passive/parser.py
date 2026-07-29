"""Passive Modbus RTU over TCP parser for BYD SH6K."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_DEBUG_SENSOR,
    CONF_HOST,
    CONF_PORT,
    CONF_PUBLISH_INTERVAL,
    CONF_RECONNECT_DELAY,
    DEFAULT_DEBUG_SENSOR,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_PUBLISH_INTERVAL,
    DEFAULT_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegisterConfig:
    """Single Modbus register configuration."""

    key: str
    scale: float
    signed: bool
    decimals: int


REGISTER_MAP: dict[str, RegisterConfig] = {
    # Batteria / BMS - slave 144, FC3
    "144_3_24580": RegisterConfig("soc_sistema", 0.1, False, 1),
    "144_3_24581": RegisterConfig("soh_sistema", 0.1, False, 1),
    "144_3_24582": RegisterConfig("capacita_installata", 0.1, False, 1),
    "144_3_24583": RegisterConfig("capacita_totale", 0.1, False, 1),
    "144_3_24584": RegisterConfig("capacita_residua", 0.1, False, 1),
    "144_3_24577": RegisterConfig("tensione_sistema", 0.1, False, 1),
    "144_3_24606": RegisterConfig("tensione_carica_consentita", 0.1, False, 1),
    "144_3_24607": RegisterConfig("tensione_scarica_consentita", 0.1, False, 1),
    "144_3_24608": RegisterConfig("corrente_carica_consentita", 0.01, False, 2),
    "144_3_24609": RegisterConfig("corrente_scarica_consentita", 0.01, False, 2),
    # Inverter / Meter / altri dati - slave 1, FC4
    "1_4_2048": RegisterConfig("assorbimento_rete", 1, True, 0),
    "1_4_49": RegisterConfig("tensione_rete", 0.1, False, 1),
    "1_4_203": RegisterConfig("potenza_pannelli", 1, False, 0),
    "1_4_204": RegisterConfig("assorbimento_casa", 1, False, 0),
    "1_4_206": RegisterConfig("potenza_batteria", 1, True, 0),
}

SENSOR_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "soc_sistema": {"name": "SOC Sistema", "unit": "%", "device_class": "battery", "state_class": "measurement", "icon": None},
    "soh_sistema": {"name": "SOH Sistema", "unit": "%", "device_class": None, "state_class": "measurement", "icon": "mdi:battery-heart"},
    "capacita_installata": {"name": "Capacita Installata", "unit": "Ah", "device_class": None, "state_class": "measurement", "icon": "mdi:battery-plus"},
    "capacita_totale": {"name": "Capacita Totale", "unit": "Ah", "device_class": None, "state_class": "measurement", "icon": "mdi:battery"},
    "capacita_residua": {"name": "Capacita Residua", "unit": "Ah", "device_class": None, "state_class": "measurement", "icon": "mdi:battery-medium"},
    "tensione_sistema": {"name": "Tensione Sistema", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": None},
    "tensione_carica_consentita": {"name": "Tensione Carica Consentita", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": None},
    "tensione_scarica_consentita": {"name": "Tensione Scarica Consentita", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": None},
    "corrente_carica_consentita": {"name": "Corrente Carica Consentita", "unit": "A", "device_class": "current", "state_class": "measurement", "icon": None},
    "corrente_scarica_consentita": {"name": "Corrente Scarica Consentita", "unit": "A", "device_class": "current", "state_class": "measurement", "icon": None},
    "assorbimento_rete": {"name": "Assorbimento Rete", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": None},
    "tensione_rete": {"name": "Tensione Rete", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": None},
    "potenza_pannelli": {"name": "Potenza Pannelli", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": None},
    "assorbimento_casa": {"name": "Assorbimento Casa", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": None},
    "potenza_batteria": {"name": "Potenza Batteria", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": None},
    "energia_prelevata_rete_giornaliera": {"name": "Energia Prelevata Rete Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "icon": None},
    "energia_immessa_rete_giornaliera": {"name": "Energia Immessa Rete Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "icon": None},
    "energia_pannelli_giornaliera": {"name": "Energia Pannelli Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "icon": None},
    "energia_caricata_batteria_giornaliera": {"name": "Energia Caricata Batteria Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "icon": None},
    "energia_prelevata_batteria_giornaliera": {"name": "Energia Prelevata Batteria Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "icon": None},
}

DEBUG_KEY = "debug_observed_registers"


class BydPassiveClient:
    """Client that listens to a passive RTU-over-TCP stream and publishes state callbacks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: Any, persisted: dict[str, Any]) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._listeners: list[Callable[[], None]] = []
        self.buffer = bytearray()
        self.last_req: dict[str, dict[str, Any]] = persisted.get("last_req", {})
        self.states: dict[str, Any] = persisted.get("states", {})
        self.raw_registers: dict[str, Any] = persisted.get("raw_registers", {})
        self.observed: dict[str, Any] = persisted.get("observed", {})
        self.daily_energy: dict[str, Any] = persisted.get("daily_energy", {})
        self.last_publish = 0.0
        self.connected = False

    @property
    def host(self) -> str:
        return self.entry.options.get(CONF_HOST, self.entry.data.get(CONF_HOST, DEFAULT_HOST))

    @property
    def port(self) -> int:
        return self.entry.options.get(CONF_PORT, self.entry.data.get(CONF_PORT, DEFAULT_PORT))

    @property
    def publish_interval(self) -> float:
        return float(self.entry.options.get(CONF_PUBLISH_INTERVAL, self.entry.data.get(CONF_PUBLISH_INTERVAL, DEFAULT_PUBLISH_INTERVAL)))

    @property
    def reconnect_delay(self) -> float:
        return float(self.entry.options.get(CONF_RECONNECT_DELAY, self.entry.data.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY)))

    @property
    def debug_enabled(self) -> bool:
        return bool(self.entry.options.get(CONF_DEBUG_SENSOR, self.entry.data.get(CONF_DEBUG_SENSOR, DEFAULT_DEBUG_SENSOR)))

    async def async_start(self) -> None:
        """Start TCP listener task."""
        self._stopping = False
        self._task = self.hass.async_create_task(self._run())

    async def async_stop(self) -> None:
        """Stop TCP listener task."""
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._async_store()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Add update listener."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def _run(self) -> None:
        while not self._stopping:
            reader = None
            writer = None
            try:
                _LOGGER.info("Connecting to BYD passive stream %s:%s", self.host, self.port)
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self.connected = True
                self._notify()
                while not self._stopping:
                    data = await reader.read(1024)
                    if not data:
                        raise ConnectionError("TCP stream closed")
                    self._feed(data)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self.connected = False
                self._notify()
                _LOGGER.warning("BYD SH6K passive stream error: %s", err)
                await asyncio.sleep(self.reconnect_delay)
            finally:
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:  # noqa: BLE001
                        pass

    def _feed(self, data: bytes) -> None:
        self.buffer.extend(data)
        safety = 0
        while len(self.buffer) >= 5 and safety < 20000:
            safety += 1
            unit = self.buffer[0]
            fc = self.buffer[1]
            if unit < 1 or unit > 247 or fc not in (3, 4):
                del self.buffer[0]
                continue

            if len(self.buffer) >= 8:
                req_frame = bytes(self.buffer[:8])
                if valid_crc(req_frame):
                    address = u16be(req_frame, 2)
                    quantity = u16be(req_frame, 4)
                    self.last_req[f"{unit}_{fc}"] = {"unit": unit, "fc": fc, "address": address, "quantity": quantity, "time": time.time()}
                    self.observed[f"{unit}_{fc}_{address}"] = {"unit": unit, "fc": fc, "address": address, "quantity": quantity, "last_seen": datetime.now().isoformat()}
                    del self.buffer[:8]
                    continue

            byte_count = self.buffer[2]
            response_length = 3 + byte_count + 2
            if byte_count > 0 and byte_count <= 250 and len(self.buffer) >= response_length:
                resp = bytes(self.buffer[:response_length])
                if valid_crc(resp):
                    req = self.last_req.get(f"{unit}_{fc}")
                    if req and byte_count == int(req.get("quantity", 0)) * 2:
                        data_part = resp[3:3 + byte_count]
                        self._parse_response(unit, fc, int(req["address"]), int(req["quantity"]), data_part)
                    del self.buffer[:response_length]
                    continue

            del self.buffer[0]

        if len(self.buffer) > 4096:
            self.buffer = self.buffer[-1024:]

        now = time.time()
        if now - self.last_publish >= self.publish_interval:
            self.last_publish = now
            self.hass.async_create_task(self._async_store())
            self._notify()

    def _parse_response(self, unit: int, fc: int, start_address: int, quantity: int, data_part: bytes) -> None:
        for idx in range(quantity):
            address = start_address + idx
            raw_unsigned = u16be(data_part, idx * 2)
            raw_signed = signed16(raw_unsigned)
            map_key = f"{unit}_{fc}_{address}"
            self.raw_registers[map_key] = {
                "unit": unit,
                "fc": fc,
                "address": address,
                "raw_unsigned": raw_unsigned,
                "raw_signed": raw_signed,
                "last_seen": datetime.now().isoformat(),
            }
            cfg = REGISTER_MAP.get(map_key)
            if not cfg:
                continue

            raw = raw_signed if cfg.signed else raw_unsigned
            value = round(raw * cfg.scale, cfg.decimals)
            self.states[cfg.key] = value

            if cfg.key == "assorbimento_rete":
                now = time.time()
                self.states["energia_prelevata_rete_giornaliera"] = self._integrate_power("energia_prelevata_rete_giornaliera", abs(value) if value < 0 else 0, now)
                self.states["energia_immessa_rete_giornaliera"] = self._integrate_power("energia_immessa_rete_giornaliera", value if value > 0 else 0, now)
            elif cfg.key == "potenza_pannelli":
                self.states["energia_pannelli_giornaliera"] = self._integrate_power("energia_pannelli_giornaliera", max(0, value), time.time())
            elif cfg.key == "potenza_batteria":
                now = time.time()
                self.states["energia_caricata_batteria_giornaliera"] = self._integrate_power("energia_caricata_batteria_giornaliera", abs(value) if value < 0 else 0, now)
                self.states["energia_prelevata_batteria_giornaliera"] = self._integrate_power("energia_prelevata_batteria_giornaliera", value if value > 0 else 0, now)

    def _integrate_power(self, energy_key: str, power_w: float, now: float) -> float:
        day = datetime.now().date().isoformat()
        item = self.daily_energy.get(energy_key)
        if not item or item.get("date") != day:
            item = {"date": day, "kwh": 0.0, "last_time": now, "last_power": 0.0}
            self.daily_energy[energy_key] = item

        dt = now - float(item.get("last_time", now))
        if 0 < dt < 600:
            item["kwh"] = float(item.get("kwh", 0.0)) + max(0.0, float(item.get("last_power", 0.0))) * (dt / 3600) / 1000
        item["last_time"] = now
        item["last_power"] = max(0.0, float(power_w or 0))
        return round(float(item["kwh"]), 3)

    async def _async_store(self) -> None:
        await self.store.async_save({
            "states": self.states,
            "raw_registers": self.raw_registers,
            "observed": self.observed,
            "daily_energy": self.daily_energy,
            "last_req": self.last_req,
        })

    def debug_state(self) -> dict[str, Any]:
        """Return complete debug state."""
        return {
            "connected": self.connected,
            "observed": self.observed,
            "raw_registers": self.raw_registers,
            "states": self.states,
            "daily_energy": self.daily_energy,
            "buffer_length": len(self.buffer),
            "last_publish": datetime.now().isoformat(),
        }


def crc16(buf: bytes) -> int:
    crc = 0xFFFF
    for b in buf:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def valid_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    body = frame[:-2]
    got = frame[-2] | (frame[-1] << 8)
    return crc16(body) == got


def u16be(buf: bytes, offset: int) -> int:
    return (buf[offset] << 8) | buf[offset + 1]


def signed16(value: int) -> int:
    return value - 65536 if value > 32767 else value
