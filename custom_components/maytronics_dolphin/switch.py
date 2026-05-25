"""Power + Autoclean switches."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .connection import DolphinBleConnection
from .config_params import WEEKLY_DAY_LABELS, ps_state_implies_power_on
from .const import (
    COMMAND_CHAR_UUID,
    CONF_ADDRESS,
    CONF_NAME,
    DATA_BLE_SESSION,
    DATA_COORDINATOR,
    DATA_NATIVE_SCHEDULE,
    DOMAIN,
)
from .coordinator import DolphinCoordinator
from .protocol import BTCommandType, build_bt_command_19

_LOGGER = logging.getLogger(__name__)

SWITCH_POWER = "power"
SWITCH_AUTOCLEAN = "autoclean"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create switches."""
    coordinator: DolphinCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [
            DolphinPowerSwitch(coordinator, entry),
            DolphinAutocleanSwitch(entry),
            DolphinNativeWeeklyRepeatSwitch(coordinator, entry),
            *[
                DolphinNativeScheduleDaySwitch(coordinator, entry, day, label)
                for day, label in enumerate(WEEKLY_DAY_LABELS)
            ],
        ],
        update_before_add=False,
    )


class _DolphinBaseSwitch(SwitchEntity):
    """Shared device info."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_available = True

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
        self._attr_is_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        return self._attr_is_on

    async def _send(self, payload: bytes) -> None:
        session: DolphinBleConnection = self.hass.data[DOMAIN][self._entry.entry_id][
            DATA_BLE_SESSION
        ]
        await session.async_send_gatt_packet(payload, COMMAND_CHAR_UUID)


class DolphinPowerSwitch(CoordinatorEntity, _DolphinBaseSwitch):
    """19-byte FFF8 power commands + ``ConfigParamsRead`` PS_State sync (``fffa``)."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        _DolphinBaseSwitch.__init__(self, entry, SWITCH_POWER, "Alimentation")

    @property
    def assumed_state(self) -> bool:
        ps = (self.coordinator.data or {}).get("ps_state")
        return ps is None

    @property
    def is_on(self) -> bool | None:
        ps = (self.coordinator.data or {}).get("ps_state") if self.coordinator.data else None
        inferred = ps_state_implies_power_on(ps)
        if inferred is not None:
            return inferred
        return self._attr_is_on

    async def _send_power_command(self, payload: bytes) -> None:
        try:
            await self._send(payload)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Maytronics Dolphin power command failed: %s", err)
        finally:
            self.hass.async_create_task(self._confirm_power_state())

    async def _confirm_power_state(self) -> None:
        for delay in (5, 15, 30):
            await asyncio.sleep(delay)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.coordinator.async_set_optimistic_power(True)
        self.async_write_ha_state()
        self.hass.async_create_task(
            self._send_power_command(build_bt_command_19(BTCommandType.STARTUP))
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.coordinator.async_set_optimistic_power(False)
        self.async_write_ha_state()
        self.hass.async_create_task(
            self._send_power_command(build_bt_command_19(BTCommandType.SHUTDOWN))
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_is_on = None


class DolphinAutocleanSwitch(_DolphinBaseSwitch):
    """Autoclean enable (19-byte `BTCommand` frame)."""

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, SWITCH_AUTOCLEAN, "Nettoyage auto")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send(
            build_bt_command_19(
                BTCommandType.AUTOCLEAN_ENABLE, autoclean_on=True
            )
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(
            build_bt_command_19(
                BTCommandType.AUTOCLEAN_ENABLE, autoclean_on=False
            )
        )
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_is_on = None


class _DolphinCoordinatorSwitch(CoordinatorEntity[DolphinCoordinator], SwitchEntity):
    """Shared coordinator-backed switch device info."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: DolphinCoordinator,
        entry: ConfigEntry,
        key: str,
        title: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = title
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or "Dolphin",
            manufacturer="Maytronics",
            model="Dolphin (BLE)",
            connections={
                (dr.CONNECTION_BLUETOOTH, dr.format_mac(entry.data[CONF_ADDRESS]))
            },
        )

    @property
    def _editor(self) -> dict:
        return self.hass.data[DOMAIN][self._entry.entry_id][DATA_NATIVE_SCHEDULE]


class DolphinNativeWeeklyRepeatSwitch(_DolphinCoordinatorSwitch):
    """Native weekly-repeat flag; writes immediately."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "native_weekly_repeat_control",
            "Repetition horaire native",
        )

    @property
    def is_on(self) -> bool:
        return bool(self._editor["repeat"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_repeat(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_repeat(False)

    async def _set_repeat(self, enabled: bool) -> None:
        session: DolphinBleConnection = self.hass.data[DOMAIN][self._entry.entry_id][
            DATA_BLE_SESSION
        ]
        await session.async_set_weekly_repeat(enabled)
        self._editor["repeat"] = enabled
        data = dict(self.coordinator.data or {})
        data["weekly_repeat"] = enabled
        data["native_schedule_poll_ok"] = True
        self.coordinator.async_set_updated_data(data)


class DolphinNativeScheduleDaySwitch(_DolphinCoordinatorSwitch):
    """Enable one day in the editable native weekly schedule."""

    def __init__(
        self,
        coordinator: DolphinCoordinator,
        entry: ConfigEntry,
        day: int,
        label: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            f"native_schedule_day_{day}",
            f"{label.capitalize()} horaire actif",
        )
        self._day = day

    @property
    def is_on(self) -> bool:
        return bool(self._editor["days"][self._day]["enabled"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._editor["days"][self._day]["enabled"] = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._editor["days"][self._day]["enabled"] = False
        self.async_write_ha_state()
