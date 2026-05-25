"""Card self-test subcommand selector (1..7)."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_params import (
    CLEAN_MODE_SELECT_OPTIONS,
    clean_mode_key,
    clean_mode_label_from_key,
)
from .connection import DolphinBleConnection
from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DATA_BLE_SESSION,
    DATA_CARD_SUB,
    DATA_COORDINATOR,
    DOMAIN,
)
from .coordinator import DolphinCoordinator

_LOGGER = logging.getLogger(__name__)

OPTIONS: list[tuple[str, int]] = [
    ("vdd", 1),
    ("tilt", 2),
    ("impeller", 3),
    ("drive", 4),
    ("gyro", 5),
    ("servo", 6),
    ("servo_calib", 7),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Card test preset."""
    coordinator: DolphinCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [
            DolphinCleanModeSelect(coordinator, entry),
            DolphinCardTestSelect(entry),
        ],
        update_before_add=False,
    )


class DolphinCleanModeSelect(CoordinatorEntity[DolphinCoordinator], SelectEntity):
    """Select the cleaning program using APK-equivalent ConfigParamsWrite packets."""

    _attr_has_entity_name = True
    _attr_name = "Mode de nettoyage"
    _attr_should_poll = False

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._address = entry.data[CONF_ADDRESS]
        name = entry.data.get(CONF_NAME) or "Dolphin"
        self._attr_unique_id = f"{entry.entry_id}_clean_mode_select"
        self._attr_options = CLEAN_MODE_SELECT_OPTIONS
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=name,
            manufacturer="Maytronics",
            model="Dolphin (BLE)",
            connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(self._address))},
        )

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        if not data:
            return None
        return clean_mode_label_from_key(data.get("clean_mode_key"))

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise HomeAssistantError(f"Mode de nettoyage inconnu: {option}")
        key = clean_mode_key(option)
        self.coordinator.async_set_optimistic_clean_mode(key)
        session: DolphinBleConnection = self.hass.data[DOMAIN][self._entry.entry_id][
            DATA_BLE_SESSION
        ]
        self.hass.async_create_background_task(
            self._async_send_clean_mode(session, key),
            f"maytronics_dolphin_set_clean_mode_{self._entry.entry_id[:8]}",
        )

    async def _async_send_clean_mode(
        self, session: DolphinBleConnection, key: str
    ) -> None:
        try:
            await session.async_set_clean_mode(key)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not set Dolphin clean mode %s: %s", key, err)
            await self.coordinator.async_request_refresh()
            return
        self.coordinator.async_set_optimistic_clean_mode(key)
        await self.coordinator.async_request_refresh()


class DolphinCardTestSelect(SelectEntity):
    """Select which `Card_Test` sub-byte to send with *Run card test*."""

    _attr_has_entity_name = True
    _attr_name = "Type de test carte"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__()
        self._entry = entry
        self._address = entry.data[CONF_ADDRESS]
        name = entry.data.get(CONF_NAME) or "Dolphin"
        self._attr_unique_id = f"{entry.entry_id}_card_test_type"
        self._attr_options = [o[0] for o in OPTIONS]
        self._map = {o[0]: o[1] for o in OPTIONS}
        self._attr_current_option = "drive"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=name,
            manufacturer="Maytronics",
            model="Dolphin (BLE)",
            connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(self._address))},
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.hass.data[DOMAIN][self._entry.entry_id][DATA_CARD_SUB] = self._map[
            self._attr_current_option
        ]

    async def async_select_option(self, option: str) -> None:
        if option not in self._map:
            return
        self._attr_current_option = option
        self.hass.data[DOMAIN][self._entry.entry_id][DATA_CARD_SUB] = self._map[
            option
        ]
        self.async_write_ha_state()
