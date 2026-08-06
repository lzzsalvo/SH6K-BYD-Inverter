"""Passive Modbus RTU over TCP parser for BYD Power-Box SH6K."""
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
    key: str
    scale: float = 1.0
    signed: bool = False
    decimals: int = 0
    type: str = "number"
    registers: int = 1
    values: dict[int, str] | None = None
    mask: int = 1
    on: str = "ON"
    off: str = "OFF"

REGISTER_MAP: dict[str, RegisterConfig | list[RegisterConfig]] = {
    # Batteria / BMS - slave 144, FC3
    "144_3_24580": RegisterConfig("soc_sistema", 0.1, False, 1),
    "144_3_24581": RegisterConfig("soh_sistema", 0.1, False, 1),
    "144_3_24582": RegisterConfig("capacita_installata", 0.1, False, 1),
    "144_3_24583": RegisterConfig("capacita_totale", 0.1, False, 1),
    "144_3_24584": RegisterConfig("capacita_residua", 0.1, False, 1),
    "144_3_24577": RegisterConfig("tensione_sistema", 0.1, False, 1),
    "144_3_24579": RegisterConfig("corrente_sistema", 0.01, True, 2),
    "144_3_24606": RegisterConfig("tensione_carica_consentita", 0.1, False, 1),
    "144_3_24607": RegisterConfig("tensione_scarica_consentita", 0.1, False, 1),
    "144_3_24608": RegisterConfig("corrente_carica_consentita", 0.01, False, 2),
    "144_3_24609": RegisterConfig("corrente_scarica_consentita", 0.01, False, 2),
    "144_3_24578": RegisterConfig("tensione_bms", 0.1, False, 1),
    "144_3_24594": RegisterConfig("temperatura_bms_1", 0.1, False, 1),
    "144_3_24597": RegisterConfig("temperatura_bms_2", 0.1, False, 1),
    "144_3_24603": RegisterConfig("temperatura_bms_3", 0.1, False, 1),
    "144_3_24605": RegisterConfig("temperatura_bms_4", 0.1, False, 1),
    "144_3_57397": RegisterConfig("anno, 1, False, 0"),
    "144_3_57398": RegisterConfig("mese, 1, False, 0"),
    "144_3_57399": RegisterConfig("giorno, 1, False, 0"),
    "144_3_57400": RegisterConfig("ora, 1, False, 0"),
    "144_3_57401": RegisterConfig("minuto, 1, False, 0"),
    "144_3_24719": RegisterConfig("giorno_installazione, 1, False, 0"),
    "144_3_24718": RegisterConfig("mese_installazione, 1, False, 0"),
    "144_3_24717": RegisterConfig("anno_installazione, 1, False, 0"),

    # Inverter / Meter - slave 1, FC4
    "1_4_2048": RegisterConfig("assorbimento_rete", 1, True, 0),
    "1_4_203": RegisterConfig("potenza_pannelli"),
    "1_4_204": RegisterConfig("assorbimento_casa"),
    "1_4_206": RegisterConfig("potenza_batteria", 1, True, 0),

    # Registri aggiunti dal nuovo flow
    "1_4_1": RegisterConfig("pv1_tensione", 0.1, False, 1),
    "1_4_2": RegisterConfig("pv1_corrente", 0.1, False, 2),
    "1_4_3": RegisterConfig("pv1_potenza"),
    "1_4_4": RegisterConfig("pv2_tensione", 0.1, False, 1),
    "1_4_5": RegisterConfig("pv2_corrente", 0.1, False, 2),
    "1_4_6": RegisterConfig("pv2_potenza"),
    "1_4_16": RegisterConfig("bus_dc_candidato", 0.1, False, 1),
    "1_4_37": RegisterConfig("tensione_inverter", 0.1, False, 1),
    "1_4_40": RegisterConfig("corrente_inverter", 0.1, False, 2),
    "1_4_49": RegisterConfig("tensione_ad_isola", 0.1, False, 1),
    "1_4_52": RegisterConfig("corrente_ad_isola", 0.1, False, 2),
    "1_4_55": RegisterConfig("potenza_ad_isola"),
    "1_4_83": RegisterConfig("temperatura_inverter_1", 0.1, False, 1),
    "1_4_84": RegisterConfig("temperatura_inverter_2", 0.1, False, 1),
    "1_4_135": RegisterConfig("versione_software_dsp1, 1, False, 0"),
    "1_4_137": RegisterConfig("versione_software_dsp2, 1, False, 0"),
    "1_4_138": RegisterConfig("versione_software_arm, 1, False, 0"),
    "1_4_144": RegisterConfig("modello_prodotto", type="ascii", registers=8),
    "1_4_152": RegisterConfig("potenza_nominale"),
    "1_4_153": [
        RegisterConfig("stato_inverter_raw"),
        RegisterConfig("stato_inverter", type="enum", values={1: "Controllo", 2: "In Rete", 3: "Eps"}),
    ],
    "1_4_182": RegisterConfig("stringa_versione_interna", type="ascii", registers=3),
    "1_4_211": RegisterConfig("numero_serie", type="ascii", registers=12),
}

SENSOR_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "soc_sistema": {"name": "SOC Sistema", "unit": "%", "device_class": "battery", "state_class": "measurement"},
    "soh_sistema": {"name": "SOH Sistema", "unit": "%", "state_class": "measurement", "icon": "mdi:battery-heart"},
    "capacita_installata": {"name": "Capacita Installata", "unit": "Ah", "state_class": "measurement", "icon": "mdi:battery-plus"},
    "capacita_totale": {"name": "Capacita Totale", "unit": "Ah", "state_class": "measurement", "icon": "mdi:battery"},
    "capacita_residua": {"name": "Capacita Residua", "unit": "Ah", "state_class": "measurement", "icon": "mdi:battery-medium"},
    "tensione_sistema": {"name": "Tensione Sistema", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "corrente_sistema": {"name": "Corrente Sistema", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "tensione_carica_consentita": {"name": "Tensione Carica Consentita", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "tensione_scarica_consentita": {"name": "Tensione Scarica Consentita", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "corrente_carica_consentita": {"name": "Corrente Carica Consentita", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "corrente_scarica_consentita": {"name": "Corrente Scarica Consentita", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "assorbimento_rete": {"name": "Assorbimento Rete", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "potenza_pannelli": {"name": "Potenza Pannelli", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "assorbimento_casa": {"name": "Assorbimento Casa", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "potenza_batteria": {"name": "Potenza Batteria", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "energia_prelevata_rete_giornaliera": {"name": "Energia Prelevata Rete Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "energia_immessa_rete_giornaliera": {"name": "Energia Immessa Rete Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "energia_pannelli_giornaliera": {"name": "Energia Pannelli Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "energia_caricata_batteria_giornaliera": {"name": "Energia Caricata Batteria Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "energia_prelevata_batteria_giornaliera": {"name": "Energia Prelevata Batteria Giornaliera", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "pv1_tensione": {"name": "PV1 Tensione", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "pv1_corrente": {"name": "PV1 Corrente", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "pv1_potenza": {"name": "PV1 Potenza", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "pv2_tensione": {"name": "PV2 Tensione", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "pv2_corrente": {"name": "PV2 Corrente", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "pv2_potenza": {"name": "PV2 Potenza", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "tensione_inverter": {"name": "Tensione Inverter", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "corrente_inverter": {"name": "Corrente Inverter", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "tensione_ad_isola": {"name": "Tensione ad Isola", "unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "corrente_ad_isola": {"name": "Corrente ad Isola", "unit": "A", "device_class": "current", "state_class": "measurement"},
    "potenza_ad_isola": {"name": "Potenza ad Isola", "unit": "W", "device_class": "power", "state_class": "measurement"},
    "stato_inverter_raw": {"name": "Stato Inverter Raw", "entity_category": "diagnostic"},
    "stato_inverter": {"name": "Stato Inverter"},
    "versione_software_dsp1": {"name": "Versione Software DSP1", "entity_category": "diagnostic"},
    "versione_software_dsp2": {"name": "Versione Software DSP2", "entity_category": "diagnostic"},
    "versione_software_arm": {"name": "Versione Software ARM", "entity_category": "diagnostic"},
    "modello_prodotto": {"name": "Modello Prodotto", "entity_category": "diagnostic"},
    "potenza_nominale": {"name": "Potenza Nominale", "unit": "W", "device_class": "power", "entity_category": "diagnostic"},
    "stringa_versione_interna": {"name": "Stringa Versione Interna", "entity_category": "diagnostic"},
    "numero_serie": {"name": "Numero di Serie", "entity_category": "diagnostic"},
    "anno": {"name": "Data Ora Anno", "entity_category": "diagnostic"},
    "mese": {"name": "Data Ora Mese", "entity_category": "diagnostic"},
    "giorno": {"name": "Data Ora Giorno", "entity_category": "diagnostic"},
    "ora": {"name": "Data Ora Ora", "entity_category": "diagnostic"},
    "minuto": {"name": "Data Ora Minuto", "entity_category": "diagnostic"},
    "giorno_installazione": {"name": "Giorno Installazione", "entity_category": "diagnostic"},
    "mese_installazione": {"name": "Mese Installazione", "entity_category": "diagnostic"},
    "anno_installazione": {"name": "Anno Installazione", "entity_category": "diagnostic"},
    "bus_dc_candidato": {"name": "Bus DC Candidato", "unit": "V", "device_class": "voltage", "state_class": "measurement", "entity_category": "diagnostic"},
    "temperatura_inverter_1": {"name": "Temperatura Inverter 1", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "entity_category": "diagnostic"},
    "temperatura_inverter_2": {"name": "Temperatura Inverter 2", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "entity_category": "diagnostic"},
    "tensione_bms": {"name": "Tensione BMS", "unit": "V", "device_class": "voltage", "state_class": "measurement", "entity_category": "diagnostic"},
    "temperatura_bms_1": {"name": "Temperatura BMS 1", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "entity_category": "diagnostic"},
    "temperatura_bms_2": {"name": "Temperatura BMS 2", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "entity_category": "diagnostic"},
    "temperatura_bms_3": {"name": "Temperatura BMS 3", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "entity_category": "diagnostic"},
    "temperatura_bms_4": {"name": "Temperatura BMS 4", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "entity_category": "diagnostic"},
}

DEPRECATED_KEYS = {"tensione_rete", "battery_enable_flag", "battery_enable_flag_raw"}
DEBUG_KEY = "debug_observed_registers"

class BydPassiveClient:
    """Listen to passive RTU-over-TCP and expose parsed states."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: Any, persisted: dict[str, Any]) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._listeners: list[Callable[[], None]] = []
        self.buffer = bytearray()
        self.last_req = persisted.get("last_req", {})
        self.states = {k: v for k, v in persisted.get("states", {}).items() if k not in DEPRECATED_KEYS}
        self.raw_registers = persisted.get("raw_registers", {})
        self.observed = persisted.get("observed", {})
        self.daily_energy = persisted.get("daily_energy", {})
        self.last_publish = 0.0
        self.connected = False

    @property
    def host(self) -> str:
        return self.entry.options.get(CONF_HOST, self.entry.data.get(CONF_HOST, DEFAULT_HOST))

    @property
    def port(self) -> int:
        return int(self.entry.options.get(CONF_PORT, self.entry.data.get(CONF_PORT, DEFAULT_PORT)))

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
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def async_stop(self) -> None:
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
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def _run(self) -> None:
        _LOGGER.warning("BYD PASSIVE CLIENT STARTED")

        while not self._stopping:
            writer = None
            try:
                _LOGGER.info("Connecting to BYD passive stream %s:%s", self.host, self.port)

                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=10,
                )

                _LOGGER.warning(
                    "BYD PASSIVE CONNECTED TO %s:%s",
                    self.host,
                    self.port,
                )

                self.connected = True
                self._notify()

                while not self._stopping:
                    data = await reader.read(1024)
                    if not data:
                        raise ConnectionError("TCP stream closed")
                    self._feed(data)

            except asyncio.CancelledError:
                raise

            except TimeoutError:
                self.connected = False
                self._notify()
                _LOGGER.warning(
                    "BYD SH6K passive connection timeout (%s:%s)",
                    self.host,
                    self.port,
                )
                await asyncio.sleep(self.reconnect_delay)

            except Exception as err:
                self.connected = False
                self._notify()
                _LOGGER.warning("BYD SH6K passive stream error: %s", err)
                await asyncio.sleep(self.reconnect_delay)

            finally:
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
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
            reg_key = f"{unit}_{fc}_{address}"
            self.raw_registers[reg_key] = {"unit": unit, "fc": fc, "address": address, "raw_unsigned": raw_unsigned, "raw_signed": raw_signed, "last_seen": datetime.now().isoformat()}
            cfgs = REGISTER_MAP.get(reg_key)
            if not cfgs:
                continue
            if not isinstance(cfgs, list):
                cfgs = [cfgs]
            for cfg in cfgs:
                value = decode_by_config(cfg, raw_unsigned, raw_signed, data_part, idx)
                if value in (None, ""):
                    continue
                self.states[cfg.key] = value
                self._update_energy(cfg.key, value)

    def _update_energy(self, key: str, value: Any) -> None:
        if not isinstance(value, (int, float)):
            return
        if key == "assorbimento_rete":
            now = time.time()
            self.states["energia_prelevata_rete_giornaliera"] = self._integrate_power("energia_prelevata_rete_giornaliera", abs(value) if value < 0 else 0, now)
            self.states["energia_immessa_rete_giornaliera"] = self._integrate_power("energia_immessa_rete_giornaliera", value if value > 0 else 0, now)
        elif key == "potenza_pannelli":
            self.states["energia_pannelli_giornaliera"] = self._integrate_power("energia_pannelli_giornaliera", max(0, value), time.time())
        elif key in ("pv1_potenza", "pv2_potenza") and self.states.get("potenza_pannelli") is None:
            pv1 = float(self.states.get("pv1_potenza") or 0)
            pv2 = float(self.states.get("pv2_potenza") or 0)
            self.states["energia_pannelli_giornaliera"] = self._integrate_power("energia_pannelli_giornaliera", max(0, pv1 + pv2), time.time())
        elif key == "potenza_batteria":
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
        await self.store.async_save({"states": self.states, "raw_registers": self.raw_registers, "observed": self.observed, "daily_energy": self.daily_energy, "last_req": self.last_req})

    def debug_state(self) -> dict[str, Any]:
        return {"connected": self.connected, "host": self.host, "port": self.port, "observed": self.observed, "raw_registers": self.raw_registers, "states": self.states, "daily_energy": self.daily_energy, "buffer_length": len(self.buffer), "last_publish": datetime.now().isoformat()}

def decode_by_config(cfg: RegisterConfig, raw_unsigned: int, raw_signed: int, data: bytes, index: int) -> Any:
    if cfg.type == "ascii":
        return ascii_from_registers(data, index, cfg.registers)
    if cfg.type == "enum":
        raw = raw_signed if cfg.signed else raw_unsigned
        values = cfg.values or {}
        return values[raw] if raw in values else f"Sconosciuto {raw}"
    if cfg.type == "bit":
        return cfg.on if (raw_unsigned & cfg.mask) != 0 else cfg.off
    raw = raw_signed if cfg.signed else raw_unsigned
    return round(raw * cfg.scale, cfg.decimals)

def ascii_from_registers(data: bytes, start_index: int, register_count: int) -> str | None:
    start = start_index * 2
    end = start + register_count * 2
    if end > len(data):
        return None
    return data[start:end].decode("ascii", errors="ignore").replace("\x00", "").strip()

def crc16(buf: bytes) -> int:
    crc = 0xFFFF
    for b in buf:
        crc ^= b
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001) if (crc & 1) else (crc >> 1)
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
