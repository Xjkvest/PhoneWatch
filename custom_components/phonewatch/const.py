"""Constants for the Phone Watch Alarm integration."""

from homeassistant.const import Platform

DOMAIN = "phonewatch"
PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.SWITCH,
]

CATEGORY_MODEL_MAPPING = {
    "1": "Door/Window Sensor",
    "doors and windows": "Door/Window Sensor",
    "vibrationsensor": "Door/Window Sensor",
    "smoke detector": "Smoke Detector",
    "smoke detectors": "Smoke Detector",
    "smokedetectorsync": "Smoke Detector",
    "leakage detectors": "Leakage Detector",
    "temperatures": "Temperature Sensor",
    "humidity": "Humidity Sensor",
    "smartplug status": "Smart Plug",
    "lock status": "Lock",
    "cameras": "Camera",
    "camerapir": "Camera",
    "keypad": "Keypad",
}

CONF_PANEL_ID = "panel_id"
CONF_CODE_FORMAT = "code_format"

# Data type configuration options
CONF_FETCH_TEMPERATURES = "fetch_temperatures"
CONF_FETCH_HUMIDITY = "fetch_humidity"
CONF_FETCH_LEAKAGE = "fetch_leakage_detectors"
CONF_FETCH_SMOKE = "fetch_smoke_detectors"
CONF_FETCH_DOORS_WINDOWS = "fetch_doors_windows"
CONF_FETCH_CAMERAS = "fetch_cameras"
CONF_FETCH_SMARTPLUGS = "fetch_smartplugs"

# Map configuration options to data endpoint keys
CONFIG_TO_ENDPOINT_MAP = {
    CONF_FETCH_TEMPERATURES: "Temperatures",
    CONF_FETCH_HUMIDITY: "Humidity",
    CONF_FETCH_LEAKAGE: "Leakage Detectors",
    CONF_FETCH_SMOKE: "Smoke Detectors",
    CONF_FETCH_DOORS_WINDOWS: "Doors and Windows",
    CONF_FETCH_CAMERAS: "Cameras", 
    CONF_FETCH_SMARTPLUGS: "Smartplug Status",
}

# Default values for selective fetching
DEFAULT_FETCH_TEMPERATURES = False  # Off by default as noted in readme
DEFAULT_FETCH_HUMIDITY = True
DEFAULT_FETCH_LEAKAGE = True
DEFAULT_FETCH_SMOKE = True
DEFAULT_FETCH_DOORS_WINDOWS = True
DEFAULT_FETCH_CAMERAS = True
DEFAULT_FETCH_SMARTPLUGS = True
