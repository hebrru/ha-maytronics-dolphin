"""One-shot BLE actions (BTCommand on FFF8)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .connection import DolphinBleConnection
from .const import (
    COMMAND_CHAR_UUID,
    CONF_ADDRESS,
    CONF_NAME,
    DATA_BLE_SESSION,
    DATA_CARD_SUB,
    DATA_COORDINATOR,
    DATA_JOY,
    DATA_NATIVE_SCHEDULE,
    DOMAIN,
    OPT_RECONNECT_BUTTON,
)
from .native_schedule import entries_from_editor
from .options import get_integration_options
from .protocol import (
    BTCommandType,
    build_bt_command_19,
    build_joystick_packet,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register buttons."""
    entities: list[ButtonEntity] = [
        DolphinShortCommandButton(
            entry,
            "quit_rc_mode",
            "Quitter le mode telecommande",
            BTCommandType.QUITE_RC_MODE,
        ),
        DolphinShortCommandButton(
            entry,
            "reset_faults",
            "Reinitialiser les defauts",
            BTCommandType.RESET_FAULTS,
        ),
        DolphinShortCommandButton(
            entry,
            "home",
            "Retour",
            BTCommandType.HOME,
        ),
        DolphinShortCommandButton(
            entry,
            "reset_dolphin",
            "Reinitialiser le robot",
            BTCommandType.RESET_DOLPHIN,
        ),
        DolphinShortCommandButton(
            entry,
            "reset_filter_indication",
            "Reinitialiser le filtre",
            BTCommandType.RESET_FILTER_INDICATION,
        ),
        DolphinShortCommandButton(
            entry,
            "ping",
            "Ping",
            BTCommandType.PING,
        ),
        DolphinShortCommandButton(
            entry,
            "wall_sensor_poll",
            "Lire le capteur mur",
            BTCommandType.WALL_SENSOR,
        ),
        DolphinLedTestButton(entry),
        DolphinJoystickSendButton(entry),
        DolphinCardTestRunButton(entry),
        DolphinReadNativeScheduleButton(entry),
        DolphinWriteNativeScheduleButton(entry),
        DolphinClearNativeScheduleButton(entry),
        DolphinSyncNativeRtcButton(entry),
        DolphinEnablePowerSupplyFeaturesButton(entry),
    ]
    if get_integration_options(entry)[OPT_RECONNECT_BUTTON]:
        entities.append(DolphinBleReconnectButton(entry))
    async_add_entities(entities, update_before_add=False)


class _DolphinButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, key: str, title: str) -> None:
        super().__init__()
        self._entry = entry
        self._address = entry.data[CONF_ADDRESS]
        name = entry.data.get(CONF_NAME) or "Dolphin"
        self._attr_name = title
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=name,
            manufacturer="Maytronics",
            model="Dolphin (BLE)",
            connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(self._address))},
        )

    async def _send(
        self,
        payload: bytes,
        *,
        pre: float = 0.3,
        post: float = 0.3,
    ) -> None:
        session: DolphinBleConnection = self.hass.data[DOMAIN][self._entry.entry_id][
            DATA_BLE_SESSION
        ]
        await session.async_send_gatt_packet(
            payload,
            COMMAND_CHAR_UUID,
            pre_write_delay=pre,
            post_write_delay=post,
        )


class DolphinShortCommandButton(_DolphinButton):
    """19-byte ``BTCommand.getBytes()`` (``BLEManager.writePacket`` style)."""

    def __init__(
        self, entry: ConfigEntry, key: str, title: str, cmd: BTCommandType
    ) -> None:
        super().__init__(entry, key, title)
        self._cmd = cmd

    async def async_press(self) -> None:
        await self._send(build_bt_command_19(self._cmd))


class DolphinLedTestButton(_DolphinButton):
    """Single-byte LED payload test (value=1)."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "led_test", "Test LED (0x01)")

    async def async_press(self) -> None:
        await self._send(build_bt_command_19(BTCommandType.LEDS, led_value=1))


class DolphinJoystickSendButton(_DolphinButton):
    """Send joystick vector from number entities."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "joystick_send", "Envoyer joystick")

    async def async_press(self) -> None:
        joy = self.hass.data[DOMAIN][self._entry.entry_id][DATA_JOY]
        payload = build_joystick_packet(joy["x"], joy["y"])
        await self._send(payload, pre=0.15, post=0.05)


class DolphinCardTestRunButton(_DolphinButton):
    """Run selected card self-test."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "card_test_run", "Lancer test carte")

    async def async_press(self) -> None:
        sub = int(self.hass.data[DOMAIN][self._entry.entry_id][DATA_CARD_SUB])
        await self._send(
            build_bt_command_19(BTCommandType.CARD_TEST, card_subcommand=sub)
        )


class DolphinReadNativeScheduleButton(_DolphinButton):
    """Read native weekly timer settings without writing anything."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "read_native_schedule", "Lire horaire natif")
        self._attr_icon = "mdi:calendar-clock"

    async def async_press(self) -> None:
        coord = self.hass.data[DOMAIN][self._entry.entry_id].get(DATA_COORDINATOR)
        if coord is not None:
            await coord.async_refresh_native_schedule()


class DolphinWriteNativeScheduleButton(_DolphinButton):
    """Write the editable native weekly schedule to the robot."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "write_native_schedule", "Envoyer horaire natif")
        self._attr_icon = "mdi:calendar-export"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN][self._entry.entry_id]
        editor = entry_data[DATA_NATIVE_SCHEDULE]
        session: DolphinBleConnection = entry_data[DATA_BLE_SESSION]
        await session.async_set_weekly_program(
            entries_from_editor(editor),
            repeat=bool(editor["repeat"]),
            sync_rtc=True,
        )
        coord = entry_data.get(DATA_COORDINATOR)
        if coord is not None:
            await coord.async_refresh_native_schedule()


class DolphinClearNativeScheduleButton(_DolphinButton):
    """Clear native weekly schedule on the robot."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "clear_native_schedule", "Effacer horaire natif")
        self._attr_icon = "mdi:calendar-remove"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN][self._entry.entry_id]
        session: DolphinBleConnection = entry_data[DATA_BLE_SESSION]
        await session.async_clear_weekly_program()
        editor = entry_data[DATA_NATIVE_SCHEDULE]
        for day_cfg in editor["days"]:
            day_cfg["enabled"] = False
        editor["repeat"] = False
        coord = entry_data.get(DATA_COORDINATOR)
        if coord is not None:
            await coord.async_refresh_native_schedule()


class DolphinSyncNativeRtcButton(_DolphinButton):
    """Synchronize the robot native RTC with Home Assistant time."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "sync_native_rtc", "Synchroniser heure robot")
        self._attr_icon = "mdi:clock-sync"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN][self._entry.entry_id]
        session: DolphinBleConnection = entry_data[DATA_BLE_SESSION]
        await session.async_sync_rtc()
        coord = entry_data.get(DATA_COORDINATOR)
        if coord is not None:
            await coord.async_refresh_native_schedule()


class DolphinEnablePowerSupplyFeaturesButton(_DolphinButton):
    """Enable weekly timer, delayed start, speed and filter-status flags."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(
            entry,
            "enable_power_supply_features",
            "Activer fonctions boitier",
        )
        self._attr_icon = "mdi:tune-variant"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN][self._entry.entry_id]
        session: DolphinBleConnection = entry_data[DATA_BLE_SESSION]
        await session.async_enable_power_supply_features()
        coord = entry_data.get(DATA_COORDINATOR)
        if coord is not None:
            await coord.async_refresh_native_schedule()


class DolphinBleReconnectButton(_DolphinButton):
    """Disconnect and reconnect BLE, then refresh state poll."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "ble_reconnect", "Liberer Bluetooth")
        self._attr_icon = "mdi:bluetooth-off"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN][self._entry.entry_id]
        session: DolphinBleConnection = entry_data[DATA_BLE_SESSION]
        await session.async_release_ble_link()
        coord = entry_data.get(DATA_COORDINATOR)
        if coord is not None:
            self.hass.async_create_task(coord.async_request_refresh())
