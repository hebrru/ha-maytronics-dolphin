"""Number entities: joystick, native schedule editor, cycle and delay."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_params import WEEKLY_DAY_LABELS
from .connection import DolphinBleConnection
from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    DATA_BLE_SESSION,
    DATA_COORDINATOR,
    DATA_JOY,
    DATA_NATIVE_SCHEDULE,
    DOMAIN,
)
from .coordinator import DolphinCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create number controls."""
    coordinator: DolphinCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    entities: list[NumberEntity] = [
        DolphinJoystickAxisNumber(entry, "joystick_x", "Joystick axe X", "x"),
        DolphinJoystickAxisNumber(entry, "joystick_y", "Joystick axe Y", "y"),
        DolphinNativeCycleTimeNumber(coordinator, entry),
        DolphinNativeDelayTimeNumber(coordinator, entry),
    ]
    for day, label in enumerate(WEEKLY_DAY_LABELS):
        title = label.capitalize()
        entities.append(
            DolphinScheduleTimeNumber(
                coordinator,
                entry,
                day,
                "hour",
                f"{title} heure horaire",
                0,
                23,
            )
        )
        entities.append(
            DolphinScheduleTimeNumber(
                coordinator,
                entry,
                day,
                "minute",
                f"{title} minute horaire",
                0,
                59,
            )
        )
    async_add_entities(entities, update_before_add=False)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    address = entry.data[CONF_ADDRESS]
    name = entry.data.get(CONF_NAME) or "Dolphin"
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer="Maytronics",
        model="Dolphin (BLE)",
        connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(address))},
    )


class DolphinJoystickAxisNumber(NumberEntity):
    """Axis value -128..127 (MyDolphin `sendJoystickCommand` range style)."""

    _attr_has_entity_name = True
    _attr_native_min_value = -128
    _attr_native_max_value = 127
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_value = 0

    def __init__(
        self, entry: ConfigEntry, key: str, title: str, axis: str
    ) -> None:
        super().__init__()
        self._entry = entry
        self._axis = axis
        self._attr_name = title
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        v = int(
            self.hass.data[DOMAIN][self._entry.entry_id][DATA_JOY][self._axis]
        )
        self._attr_native_value = v

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        self.hass.data[DOMAIN][self._entry.entry_id][DATA_JOY][self._axis] = v
        self._attr_native_value = v
        self.async_write_ha_state()


class _DolphinCoordinatorNumber(
    CoordinatorEntity[DolphinCoordinator],
    NumberEntity,
):
    """Shared number entity device info."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: DolphinCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        title: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = title
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(entry)

    @property
    def _editor(self) -> dict:
        return self.hass.data[DOMAIN][self._entry.entry_id][DATA_NATIVE_SCHEDULE]


class DolphinScheduleTimeNumber(_DolphinCoordinatorNumber):
    """Editable hour/minute for one native weekly schedule day."""

    _attr_native_step = 1

    def __init__(
        self,
        coordinator: DolphinCoordinator,
        entry: ConfigEntry,
        day: int,
        field: str,
        title: str,
        minimum: int,
        maximum: int,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            key=f"native_schedule_{day}_{field}",
            title=title,
        )
        self._day = day
        self._field = field
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> int:
        return int(self._editor["days"][self._day][self._field])

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        v = max(
            int(self._attr_native_min_value),
            min(int(self._attr_native_max_value), v),
        )
        self._editor["days"][self._day][self._field] = v
        self.async_write_ha_state()


class DolphinNativeCycleTimeNumber(_DolphinCoordinatorNumber):
    """Native cycle time in minutes; writes immediately."""

    _attr_native_min_value = 6
    _attr_native_max_value = 360
    _attr_native_step = 6
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_cycle_time_control",
            title="Duree cycle native",
        )

    @property
    def native_value(self) -> int:
        return int(self._editor["cycle_time_minutes"])

    async def async_set_native_value(self, value: float) -> None:
        minutes = max(6, min(360, int(round(value / 6) * 6)))
        session: DolphinBleConnection = self.hass.data[DOMAIN][self._entry.entry_id][
            DATA_BLE_SESSION
        ]
        await session.async_set_cycle_time(minutes)
        self._editor["cycle_time_minutes"] = minutes
        data = dict(self.coordinator.data or {})
        data["cycle_time_minutes"] = minutes
        data["native_schedule_poll_ok"] = True
        self.coordinator.async_set_updated_data(data)


class DolphinNativeDelayTimeNumber(_DolphinCoordinatorNumber):
    """Native delayed start in minutes; writes immediately."""

    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_delay_time_control",
            title="Retard depart natif",
        )

    @property
    def native_value(self) -> int:
        return int(self._editor["delay_time_minutes"])

    async def async_set_native_value(self, value: float) -> None:
        minutes = max(0, min(1440, int(value)))
        session: DolphinBleConnection = self.hass.data[DOMAIN][self._entry.entry_id][
            DATA_BLE_SESSION
        ]
        await session.async_set_delay_time(minutes)
        self._editor["delay_time_minutes"] = minutes
        data = dict(self.coordinator.data or {})
        data["delay_time_minutes"] = minutes
        data["native_schedule_poll_ok"] = True
        self.coordinator.async_set_updated_data(data)
