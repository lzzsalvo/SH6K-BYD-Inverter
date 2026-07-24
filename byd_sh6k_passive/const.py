"""Constants for BYD SH6K Passive Modbus integration."""

DOMAIN = "byd_sh6k_passive"
PLATFORMS = ["sensor"]

CONF_HOST = "host"
CONF_PORT = "port"
CONF_NAME = "name"
CONF_PUBLISH_INTERVAL = "publish_interval"
CONF_RECONNECT_DELAY = "reconnect_delay"
CONF_DEBUG_SENSOR = "debug_sensor"

DEFAULT_NAME = "BYD Power-Box SH6K"
DEFAULT_HOST = "192.168.1.240"
DEFAULT_PORT = 502
DEFAULT_PUBLISH_INTERVAL = 5
DEFAULT_RECONNECT_DELAY = 10
DEFAULT_DEBUG_SENSOR = True

STORE_VERSION = 1
STORE_KEY = "byd_sh6k_passive"
