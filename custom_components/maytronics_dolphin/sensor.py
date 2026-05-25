"""Sensors: cleaner state from ``PS_State`` + diagnostic GATT reads."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_params import (
    CleanMode,
    MMIDelaySelection,
    PSState,
    ScheduleOwner,
    WeeklyProgramEntry,
    clean_mode_to_str,
    features_to_str,
    mmi_delay_selection_to_str,
    ps_state_to_str,
    schedule_owner_to_str,
    weekly_program_to_attr,
    weekly_program_to_str,
)
from .status_params import CleaningSurface, WorkingStatus
from .const import CONF_ADDRESS, CONF_NAME, DATA_COORDINATOR, DOMAIN
from .coordinator import DolphinCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors."""
    coordinator: DolphinCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [
            DolphinCleanerStateSensor(coordinator, entry),
            DolphinCleanProgramSensor(coordinator, entry),
            DolphinCleaningSurfaceSensor(coordinator, entry),
            DolphinWorkingStatusSensor(coordinator, entry),
            DolphinWeeklyProgramSensor(coordinator, entry),
            DolphinWeeklyRepeatSensor(coordinator, entry),
            DolphinScheduleOwnerSensor(coordinator, entry),
            DolphinNativeFeaturesSensor(coordinator, entry),
            DolphinMmiDelaySelectionSensor(coordinator, entry),
            DolphinCycleTimeSensor(coordinator, entry),
            DolphinDelayTimeSensor(coordinator, entry),
            DolphinStatusRawSensor(coordinator, entry, "fffc"),
            DolphinStatusRawSensor(coordinator, entry, "fffd"),
        ],
        update_before_add=False,
    )


class _DolphinDiagSensorBase(CoordinatorEntity[DolphinCoordinator], SensorEntity):
    """Shared device info for coordinator-backed sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: DolphinCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._address = entry.data[CONF_ADDRESS]
        dev_name = entry.data.get(CONF_NAME) or "Dolphin"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        if entity_category is not None:
            self._attr_entity_category = entity_category
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=dev_name,
            manufacturer="Maytronics",
            model="Dolphin (BLE)",
            connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(self._address))},
        )


class DolphinCleanerStateSensor(_DolphinDiagSensorBase):
    """Text state from ``ConfigParamsRead`` PS_State (same poll as Power sync)."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="cleaner_state",
            name="Etat du robot",
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        ps: PSState | None = data.get("ps_state") if data else None
        return ps_state_to_str(ps)


class DolphinCleanProgramSensor(_DolphinDiagSensorBase):
    """Selected clean program from ``Working_Clean_Mode`` (ConfigParamsRead cmd **5**)."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="clean_program",
            name="Programme de nettoyage",
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data and data.get("clean_mode_key"):
            return data["clean_mode_key"]
        mode: CleanMode | None = data.get("clean_mode") if data else None
        return clean_mode_to_str(mode)


class DolphinWorkingStatusSensor(_DolphinDiagSensorBase):
    """``GetStatusRead$WorkingStatus`` — at_work vs finished (for pool card / automations)."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="working_status",
            name="Statut de travail",
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        working: WorkingStatus | None = data.get("working_status") if data else None
        if working is None:
            return "unknown"
        return str(working)


class DolphinCleaningSurfaceSensor(_DolphinDiagSensorBase):
    """Best-effort floor/wall/waterline from ``InternalParamsRead`` + clean program."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="cleaning_surface",
            name="Surface de nettoyage",
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        surface: CleaningSurface | None = (
            data.get("cleaning_surface") if data else None
        )
        if surface is None:
            return "unknown"
        return str(surface)

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        data = self.coordinator.data or {}
        snap = data.get("internal_snapshot")
        attrs: dict[str, str | int | None] = {
            "working_status": (
                str(data["working_status"]) if data.get("working_status") else None
            ),
            "internal_poll_ok": data.get("internal_poll_ok"),
        }
        if snap is not None:
            attrs["phase_byte"] = snap.phase_byte
            attrs["motor_aux_byte"] = snap.motor_aux
            attrs["climb_every_byte"] = snap.climb_every
            attrs["clean_mode_byte"] = snap.clean_mode_byte
        return attrs


class DolphinWeeklyProgramSensor(_DolphinDiagSensorBase):
    """Native weekly timer read from ``ConfigParamsRead.Weekly_Program``."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_weekly_program",
            name="Horaire natif",
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        entries: list[WeeklyProgramEntry] | None = (
            data.get("weekly_program") if data else None
        )
        return weekly_program_to_str(entries)

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool | None]:
        data = self.coordinator.data or {}
        entries: list[WeeklyProgramEntry] | None = data.get("weekly_program")
        attrs: dict[str, str | int | bool | None] = weekly_program_to_attr(entries)
        attrs["lecture_ok"] = data.get("native_schedule_poll_ok")
        attrs["repetition"] = data.get("weekly_repeat")
        attrs["proprietaire"] = schedule_owner_to_str(data.get("schedule_owner"))
        attrs["cycle_minutes"] = data.get("cycle_time_minutes")
        attrs["retard_minutes"] = data.get("delay_time_minutes")
        return attrs


class DolphinWeeklyRepeatSensor(_DolphinDiagSensorBase):
    """Native weekly-repeat flag."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_weekly_repeat",
            name="Repetition horaire native",
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        repeat = data.get("weekly_repeat") if data else None
        if repeat is None:
            return "unknown"
        return "active" if repeat else "inactive"


class DolphinScheduleOwnerSensor(_DolphinDiagSensorBase):
    """Whether the native timer is owned by box buttons or remote/app."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_schedule_owner",
            name="Proprietaire horaire natif",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        owner: ScheduleOwner | None = data.get("schedule_owner") if data else None
        return schedule_owner_to_str(owner)


class DolphinNativeFeaturesSensor(_DolphinDiagSensorBase):
    """Feature flags advertised by the power supply firmware."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_features",
            name="Fonctions boitier",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        features: dict[str, bool] | None = data.get("native_features") if data else None
        return features_to_str(features)

    @property
    def extra_state_attributes(self) -> dict[str, bool | None]:
        data = self.coordinator.data or {}
        features: dict[str, bool] | None = data.get("native_features")
        return {
            "filter_status": None if features is None else features["filter_status"],
            "weekly_timer": None if features is None else features["weekly_timer"],
            "delayed_start": None if features is None else features["delayed_start"],
            "speed": None if features is None else features["speed"],
            "lecture_ok": data.get("native_schedule_poll_ok"),
        }


class DolphinMmiDelaySelectionSensor(_DolphinDiagSensorBase):
    """Physical power-supply delayed-start selection."""

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="mmi_delay_selection",
            name="Retard selection boitier",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        selection: MMIDelaySelection | None = (
            data.get("mmi_delay_selection") if data else None
        )
        return mmi_delay_selection_to_str(selection)


class DolphinCycleTimeSensor(_DolphinDiagSensorBase):
    """Native cycle time, if the power supply exposes it."""

    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_cycle_time",
            name="Duree cycle native",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return data.get("cycle_time_minutes") if data else None


class DolphinDelayTimeSensor(_DolphinDiagSensorBase):
    """Native delayed-start value, if present."""

    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: DolphinCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            key="native_delay_time",
            name="Retard depart natif",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return data.get("delay_time_minutes") if data else None


class DolphinStatusRawSensor(_DolphinDiagSensorBase):
    """Hex dump of optional GATT read on ``fffc`` / ``fffd`` (diagnostic)."""

    def __init__(
        self,
        coordinator: DolphinCoordinator,
        entry: ConfigEntry,
        which: str,
    ) -> None:
        key = f"status_raw_{which}"
        title = f"Donnees brutes ({which})"
        super().__init__(
            coordinator,
            entry,
            key=key,
            name=title,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._which = which

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if not data:
            return None
        if self._which == "fffc":
            return data.get("status_fffc_hex")
        return data.get("internal_fffd_hex")
