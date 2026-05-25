"""Poll robot ``PS_State`` and optional GATT diagnostics."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .config_params import CleanMode, PSState, clean_mode_from_key, clean_mode_key
from .status_params import (
    CleaningSurface,
    InternalParamsSnapshot,
    WorkingStatus,
    resolve_clean_mode_key,
)
from .connection import DolphinBleConnection
from .const import DATA_NATIVE_SCHEDULE, DOMAIN, OPT_STATE_POLL_SEC
from .native_schedule import sync_editor_from_native_data
from .options import get_integration_options

_LOGGER = logging.getLogger(__name__)


_NATIVE_SCHEDULE_KEYS: tuple[str, ...] = (
    "native_schedule_poll_ok",
    "weekly_program",
    "weekly_repeat",
    "schedule_owner",
    "cycle_time_minutes",
    "delay_time_minutes",
    "native_features",
    "mmi_delay_selection",
)


class DolphinCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Periodic ``ConfigParamsRead`` (PS_State) + best-effort ``fffc``/``fffd`` reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: DolphinBleConnection,
        entry: ConfigEntry,
    ) -> None:
        poll_sec = int(get_integration_options(entry)[OPT_STATE_POLL_SEC])
        interval = None if poll_sec <= 0 else timedelta(seconds=poll_sec)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )
        self._session = session
        self._entry_id = entry.entry_id

    def async_set_optimistic_power(self, is_on: bool) -> None:
        """Update visible entities immediately while the BLE command is in flight."""
        data = dict(self.data or {})
        if is_on:
            data["ps_state"] = PSState.ON
            data["working_status"] = WorkingStatus.AT_WORK
            if data.get("cleaning_surface") in (None, CleaningSurface.UNAVAILABLE):
                data["cleaning_surface"] = CleaningSurface.UNKNOWN
        else:
            data["ps_state"] = PSState.OFF
            data["working_status"] = None
            data["cleaning_surface"] = CleaningSurface.UNAVAILABLE
        data["optimistic_power"] = is_on
        data["optimistic_power_until"] = time.monotonic() + 90
        self.async_set_updated_data(data)

    def async_set_optimistic_clean_mode(self, option: str) -> None:
        """Update the mode selector immediately while the BLE write is in flight."""
        key = clean_mode_key(option)
        data = dict(self.data or {})
        data["clean_mode_key"] = key
        data["clean_mode"] = clean_mode_from_key(key)
        data["optimistic_clean_mode"] = key
        data["optimistic_clean_mode_until"] = time.monotonic() + 90
        self.async_set_updated_data(data)

    def _apply_optimistic_power(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        target = previous.get("optimistic_power")
        until = float(previous.get("optimistic_power_until") or 0)
        if target is None or time.monotonic() >= until:
            current.pop("optimistic_power", None)
            current.pop("optimistic_power_until", None)
            return current

        inferred = current.get("ps_state")
        target_confirmed = (
            (target and inferred is not None and inferred != PSState.OFF)
            or (not target and inferred == PSState.OFF)
        )
        if target_confirmed:
            current.pop("optimistic_power", None)
            current.pop("optimistic_power_until", None)
            return current

        current["optimistic_power"] = target
        current["optimistic_power_until"] = until
        if target:
            current["ps_state"] = PSState.ON
            current["working_status"] = WorkingStatus.AT_WORK
            if current.get("cleaning_surface") in (None, CleaningSurface.UNAVAILABLE):
                current["cleaning_surface"] = CleaningSurface.UNKNOWN
        else:
            current["ps_state"] = PSState.OFF
            current["working_status"] = None
            current["cleaning_surface"] = CleaningSurface.UNAVAILABLE
        return current

    def _apply_optimistic_clean_mode(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        target = previous.get("optimistic_clean_mode")
        until = float(previous.get("optimistic_clean_mode_until") or 0)
        if target is None or time.monotonic() >= until:
            current.pop("optimistic_clean_mode", None)
            current.pop("optimistic_clean_mode_until", None)
            return current

        if current.get("clean_mode_key") == target:
            current.pop("optimistic_clean_mode", None)
            current.pop("optimistic_clean_mode_until", None)
            return current

        current["clean_mode_key"] = target
        current["clean_mode"] = clean_mode_from_key(target)
        current["optimistic_clean_mode"] = target
        current["optimistic_clean_mode_until"] = until
        return current

    def _carry_native_schedule(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep native timer values from manual reads across normal state polls."""
        for key in _NATIVE_SCHEDULE_KEYS:
            if key in previous and key not in current:
                current[key] = previous[key]
        return current

    async def async_refresh_native_schedule(self) -> None:
        """Read native weekly timer settings and publish them."""
        schedule_data = await self._session.async_read_native_schedule()
        data = dict(self.data or {})
        data.update(schedule_data)
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        editor = entry_data.get(DATA_NATIVE_SCHEDULE)
        if editor is not None and schedule_data.get("native_schedule_poll_ok"):
            sync_editor_from_native_data(editor, schedule_data)
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> dict[str, Any]:
        prev = self.data or {}
        prev_ps: PSState | None = prev.get("ps_state")
        prev_clean: CleanMode | None = prev.get("clean_mode")
        prev_surface: CleaningSurface | None = prev.get("cleaning_surface")
        prev_working: WorkingStatus | None = prev.get("working_status")
        prev_internal: InternalParamsSnapshot | None = prev.get("internal_snapshot")
        prev_fffc = prev.get("status_fffc_hex")
        prev_fffd = prev.get("internal_fffd_hex")
        try:
            ps, clean_mode, surface, working, internal, fffc_raw, fffd_raw = (
                await self._session.async_poll_robot_state()
            )
            current = {
                "ps_state": ps,
                "ps_poll_ok": ps is not None,
                "clean_mode": clean_mode,
                "clean_mode_key": resolve_clean_mode_key(clean_mode, internal),
                "clean_mode_poll_ok": clean_mode is not None,
                "cleaning_surface": surface,
                "internal_poll_ok": internal is not None,
                "working_status": working,
                "internal_snapshot": internal,
                "status_fffc_hex": fffc_raw.hex() if fffc_raw else prev_fffc,
                "internal_fffd_hex": fffd_raw.hex() if fffd_raw else prev_fffd,
            }
            current = self._carry_native_schedule(prev, current)
            current = self._apply_optimistic_clean_mode(prev, current)
            return self._apply_optimistic_power(prev, current)
        except Exception as err:  # noqa: BLE001 — keep last values
            _LOGGER.debug("Maytronics Dolphin coordinator update failed: %s", err)
            current = {
                "ps_state": prev_ps,
                "ps_poll_ok": False,
                "clean_mode": prev_clean,
                "clean_mode_key": prev.get("clean_mode_key"),
                "clean_mode_poll_ok": False,
                "cleaning_surface": prev_surface,
                "internal_poll_ok": False,
                "working_status": prev_working,
                "internal_snapshot": prev_internal,
                "status_fffc_hex": prev_fffc,
                "internal_fffd_hex": prev_fffd,
            }
            return self._carry_native_schedule(prev, current)
