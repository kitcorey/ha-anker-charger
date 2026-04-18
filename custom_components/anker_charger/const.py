"""Constants for the Anker A91B2 charger integration."""

from logging import Logger, getLogger
from typing import Final

from homeassistant.const import Platform

LOGGER: Logger = getLogger(__package__)

NAME: Final[str] = "Anker Charger"
DOMAIN: Final[str] = "anker_charger"
MANUFACTURER: Final[str] = "Anker"
ATTRIBUTION: Final[str] = "Data provided by the Anker cloud API"
ACCEPT_TERMS: Final[str] = "accept_terms"
TERMS_LINK: Final[str] = "terms_link"
MQTT_LINK: Final[str] = "mqtt_link"
TC_LINK: Final[str] = (
    "https://github.com/kitcorey/ha-anker-solix/blob/main/README.md"
)
MQ_LINK: Final[str] = (
    "https://github.com/kitcorey/ha-anker-solix#mqtt"
)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
]

# Config entry option keys.
CONF_MQTT_OPTIONS: Final[str] = "mqtt_options"
CONF_MQTT_USAGE: Final[str] = "mqtt_usage"
CONF_TRIGGER_TIMEOUT: Final[str] = "trigger_timeout"

# Misc keys used in frontend placeholders and entity data.
ERROR_DETAIL: Final[str] = "error_detail"
SHARED_ACCOUNT: Final[str] = "shared_account"
IMAGEFOLDER: Final[str] = "images"
MQTT_OVERLAY: Final[str] = "mqtt_overlay"

# Feature toggles kept as False in this fork. Referenced by a few entity-code
# paths inherited from upstream; retained here so those paths compile without
# unused-import errors.
ALLOW_TESTMODE: Final[bool] = False
CREATE_ALL_ENTITIES: Final[bool] = False
TEST_NUMBERVARIANCE: Final[bool] = False
CONF_SKIP_INVALID: Final[str] = "skip_invalid"
