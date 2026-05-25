"""BLE client for Maytronics Dolphin — short sessions (connect, work, disconnect).

Holding an idle GATT link wedges some robots (BT LED stays on, unit frozen until
power cycle). We release the link after every command and poll.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, TypeVar

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .ble import _noop_notify, async_resolve_ble_device
from .config_params import (
    CONFIG_PARAMS_CMD_CYCLE_TIME,
    CONFIG_PARAMS_CMD_DELAY_TIME,
    CONFIG_PARAMS_CMD_FEATURE_ENABLE,
    CONFIG_PARAMS_CMD_MMI_DELAY_SELECTION,
    CONFIG_PARAMS_CMD_OWNER_WEEKLY_TIMER,
    CONFIG_PARAMS_CMD_PS_STATE,
    CONFIG_PARAMS_CMD_REPEAT_WEEKLY_SCHEDULER,
    CONFIG_PARAMS_CMD_RTC,
    CONFIG_PARAMS_CMD_WEEKLY_PROGRAM,
    CONFIG_PARAMS_CMD_WORKING_CLEAN_MODE,
    CleanMode,
    PSState,
    ScheduleOwner,
    WeeklyProgramEntry,
    MMIDelaySelection,
    build_clean_mode_write_request,
    build_config_params_read_request,
    build_cycle_time_write_request,
    build_delay_time_write_request,
    build_features_write_request,
    build_rtc_write_request,
    build_weekly_program_write_request,
    build_weekly_repeat_write_request,
    clean_mode_key,
    parse_config_params_clean_mode,
    parse_config_params_cycle_time,
    parse_config_params_delay_time,
    parse_config_params_features,
    parse_config_params_mmi_delay_selection,
    parse_config_params_ps_state,
    parse_config_params_schedule_owner,
    parse_config_params_weekly_program,
    parse_config_params_weekly_repeat,
    parse_config_params_write_ack,
)

_T = TypeVar("_T")
from .const import (
    CONFIG_PARAMS_READ_UUID,
    CONFIG_PARAMS_WRITE_UUID,
    DATA_BLE_SESSION,
    DOMAIN,
    GET_STATUS_READ_UUID,
    INTERNAL_PARAMS_READ_UUID,
    OPT_BLE_KEEPALIVE_SEC,
    OPT_BLE_PERSISTENT_SESSION,
    OPT_DIAGNOSTIC_PROBE,
)
from .status_params import (
    CleaningSurface,
    InternalParamsSnapshot,
    WorkingStatus,
    build_get_status_read_request,
    build_internal_params_read_request,
    infer_cleaning_surface,
    parse_get_status_working,
    parse_internal_params_snapshot,
    resolve_working_status,
)
from .options import get_integration_options

_LOGGER = logging.getLogger(__name__)

_PS_READ_PER_STRATEGY_TIMEOUT = 3.5
_PS_FAIL_LOG_INTERVAL_SEC = 300.0
_PS_MAX_STRATEGIES = 2
_GATT_READ_PROBE_TIMEOUT = 4.0
_BLE_CONNECT_TIMEOUT = 8.0


class _PsFailLogThrottle:
    last_monotonic: float = 0.0


def _throttled_ps_fail_warning(message: str) -> None:
    now = time.monotonic()
    if now - _PsFailLogThrottle.last_monotonic >= _PS_FAIL_LOG_INTERVAL_SEC:
        _PsFailLogThrottle.last_monotonic = now
        _LOGGER.warning("%s", message)


def _addr_hex_digits(value: str) -> str:
    return "".join(c for c in value if c in "0123456789abcdefABCDEF").lower()


def _looks_like_real_mac(value: str) -> bool:
    return len(_addr_hex_digits(value)) == 12


class DolphinBleConnection:
    """Connect only for each operation, then disconnect so the robot can run."""

    def __init__(self, hass: HomeAssistant, address: str, entry_id: str) -> None:
        self.hass = hass
        self.address = address
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._shutting_down = False

    def mark_shutting_down(self) -> None:
        self._shutting_down = True

    @property
    def is_connected(self) -> bool:
        c = self._client
        return c is not None and c.is_connected

    def _options(self) -> dict[str, int | bool]:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return {}
        return get_integration_options(entry)

    def _persistent_session(self) -> bool:
        return bool(self._options().get(OPT_BLE_PERSISTENT_SESSION))

    async def _release_after_operation_locked(self, *, force: bool = False) -> None:
        """Disconnect after an operation unless persistent session is enabled."""
        if force or not self._persistent_session():
            await self._disconnect_locked()

    async def async_disconnect(self) -> None:
        self._shutting_down = True
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        if self._client is None:
            return
        try:
            if self._client.is_connected:
                await self._client.disconnect()
                _LOGGER.debug("Maytronics Dolphin: BLE released")
        except BleakError:
            _LOGGER.debug("disconnect raised BleakError (ignored)", exc_info=True)
        self._client = None

    async def _ensure_connected_locked(self) -> BleakClientWithServiceCache:
        if self._shutting_down:
            raise HomeAssistantError("Maytronics Dolphin BLE is shutting down")
        if self._client is not None and self._client.is_connected:
            return self._client

        await self._disconnect_locked()

        ha_error: Exception | None = None
        try:
            ble_device = await async_resolve_ble_device(self.hass, self.address)
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                name=ble_device.name or self.address,
                timeout=_BLE_CONNECT_TIMEOUT,
            )
            if not client.is_connected:
                raise HomeAssistantError("Failed to connect over BLE")
            self._client = client
            mode = "persistent" if self._persistent_session() else "ephemeral"
            _LOGGER.debug(
                "Maytronics Dolphin: BLE connected (%s, %s)",
                ble_device.address,
                mode,
            )
            return self._client
        except (BleakError, HomeAssistantError, TimeoutError) as err:
            ha_error = err
            _LOGGER.warning(
                "Maytronics Dolphin: HA BLE connect path failed for %s: %s",
                self.address,
                err,
            )
            if not _looks_like_real_mac(self.address):
                raise

        try:
            return await self._ensure_connected_direct_locked()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Maytronics Dolphin: direct BLE connect failed for %s: %s",
                self.address,
                err,
            )
            raise HomeAssistantError(
                f"BLE connect failed via HA Bluetooth/proxy ({ha_error}) and direct address ({err})"
            ) from err

    async def _ensure_connected_direct_locked(self) -> BleakClientWithServiceCache:
        """Fallback for local BlueZ adapters when HA has a stale advertisement cache."""
        client = BleakClientWithServiceCache(
            self.address,
            timeout=_BLE_CONNECT_TIMEOUT,
        )
        try:
            await client.connect()
        except Exception:
            with suppress(Exception):
                await client.disconnect()
            raise
        if not client.is_connected:
            raise HomeAssistantError("Failed to connect over BLE")
        self._client = client
        mode = "persistent" if self._persistent_session() else "ephemeral"
        _LOGGER.debug(
            "Maytronics Dolphin: direct BLE connected (%s, %s)",
            self.address,
            mode,
        )
        return self._client

    async def async_release_ble_link(self) -> None:
        """Drop GATT if held (frees robot when link was wedged)."""
        if self._shutting_down:
            return
        try:
            async with self._lock:
                await self._disconnect_locked()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("release BLE: %s", err)

    async def async_send_gatt_packet(
        self,
        payload: bytes,
        char_uuid: str,
        *,
        pre_write_delay: float = 0.3,
        post_write_delay: float = 0.3,
    ) -> None:
        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
                await client.start_notify(char_uuid, _noop_notify)
                await asyncio.sleep(pre_write_delay)
                await client.write_gatt_char(char_uuid, payload, response=True)
                await asyncio.sleep(post_write_delay)
                try:
                    await client.stop_notify(char_uuid)
                except BleakError:
                    _LOGGER.debug("stop_notify failed (ignored)", exc_info=True)
            except BleakError as err:
                await self._release_after_operation_locked(force=True)
                raise HomeAssistantError(f"BLE error: {err}") from err
            finally:
                await self._release_after_operation_locked()

    async def _read_config_params_notify_once(
        self,
        client: BleakClient,
        notify_uuid: str,
        write_uuid: str,
        payload: bytes,
        parser: Callable[[bytes], _T | None],
        *,
        timeout: float,
        pre_write_delay: float,
        write_with_response: bool = True,
    ) -> _T | None:
        acc = bytearray()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[_T] = loop.create_future()

        def _handler(_sender: Any, data: bytearray) -> None:
            acc.extend(data)
            parsed = parser(bytes(acc))
            if parsed is not None and not fut.done():
                fut.set_result(parsed)

        await client.start_notify(notify_uuid, _handler)
        try:
            await asyncio.sleep(pre_write_delay)
            await client.write_gatt_char(
                write_uuid, payload, response=write_with_response
            )
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            try:
                await client.stop_notify(notify_uuid)
            except BleakError:
                _LOGGER.debug("stop_notify after PS read (ignored)", exc_info=True)

    async def _write_config_params_locked(
        self,
        client: BleakClient,
        command_code: int,
        payload: bytes,
        log_label: str,
        *,
        timeout: float = 5.0,
        pre_write_delay: float = 0.3,
    ) -> bool:
        def _parser(data: bytes) -> bool | None:
            return parse_config_params_write_ack(data, command_code)

        for write_with_response in (True, False):
            try:
                ok = await self._read_config_params_notify_once(
                    client,
                    CONFIG_PARAMS_WRITE_UUID,
                    CONFIG_PARAMS_WRITE_UUID,
                    payload,
                    _parser,
                    timeout=timeout,
                    pre_write_delay=pre_write_delay,
                    write_with_response=write_with_response,
                )
                if ok is True:
                    _LOGGER.debug("%s write ok (rsp=%s)", log_label, write_with_response)
                    return True
                if ok is False:
                    _LOGGER.warning("%s write rejected by robot", log_label)
                    return False
            except BleakError as err:
                _LOGGER.debug(
                    "%s write BLE error (rsp=%s): %s",
                    log_label,
                    write_with_response,
                    err,
                    exc_info=True,
                )
                return False
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "%s write failed (rsp=%s): %s",
                    log_label,
                    write_with_response,
                    err,
                    exc_info=True,
                )
        _LOGGER.warning("%s write timed out waiting for Dolphin ACK", log_label)
        return False

    async def _read_status_probe_locked(
        self, client: BleakClient
    ) -> tuple[bytes | None, bytes | None]:
        ffc: bytes | None = None
        ffd: bytes | None = None
        try:
            raw = await asyncio.wait_for(
                client.read_gatt_char(GET_STATUS_READ_UUID),
                timeout=_GATT_READ_PROBE_TIMEOUT,
            )
            ffc = bytes(raw) if raw else None
        except (BleakError, asyncio.TimeoutError):
            _LOGGER.debug("GATT read fffc failed", exc_info=True)
        try:
            raw = await asyncio.wait_for(
                client.read_gatt_char(INTERNAL_PARAMS_READ_UUID),
                timeout=_GATT_READ_PROBE_TIMEOUT,
            )
            ffd = bytes(raw) if raw else None
        except (BleakError, asyncio.TimeoutError):
            _LOGGER.debug("GATT read fffd failed", exc_info=True)
        return (ffc, ffd)

    def _config_params_strategies(
        self, command_code: int
    ) -> list[tuple[str, str, bytes, str, bool]]:
        req = build_config_params_read_request(command_code)
        all_s: list[tuple[str, str, bytes, str, bool]] = [
            (
                CONFIG_PARAMS_READ_UUID,
                CONFIG_PARAMS_READ_UUID,
                req,
                "notify=fffa write=fffa rsp=True",
                True,
            ),
            (
                CONFIG_PARAMS_READ_UUID,
                CONFIG_PARAMS_WRITE_UUID,
                req,
                "notify=fffa write=fff9 rsp=True",
                True,
            ),
            (
                CONFIG_PARAMS_READ_UUID,
                CONFIG_PARAMS_READ_UUID,
                req,
                "notify=fffa write=fffa rsp=False",
                False,
            ),
            (
                CONFIG_PARAMS_READ_UUID,
                CONFIG_PARAMS_WRITE_UUID,
                req,
                "notify=fffa write=fff9 rsp=False",
                False,
            ),
        ]
        return all_s[:_PS_MAX_STRATEGIES]

    async def _read_config_params_locked(
        self,
        client: BleakClient,
        command_code: int,
        parser: Callable[[bytes], _T | None],
        log_label: str,
        *,
        timeout: float | None = None,
        pre_write_delay: float = 0.2,
        warn_on_fail: bool = True,
    ) -> _T | None:
        per = timeout if timeout is not None else _PS_READ_PER_STRATEGY_TIMEOUT
        for notify_u, write_u, payload, label, rsp in self._config_params_strategies(
            command_code
        ):
            try:
                got = await self._read_config_params_notify_once(
                    client,
                    notify_u,
                    write_u,
                    payload,
                    parser,
                    timeout=per,
                    pre_write_delay=pre_write_delay,
                    write_with_response=rsp,
                )
                if got is not None:
                    _LOGGER.debug("%s read ok (%s)", log_label, label)
                    return got
            except BleakError as err:
                _LOGGER.debug(
                    "%s strategy %s BLE error: %s", log_label, label, err, exc_info=True
                )
                return None
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "%s strategy %s failed: %s", log_label, label, err, exc_info=True
                )
        if warn_on_fail:
            _throttled_ps_fail_warning(
                f"Maytronics Dolphin: {log_label} read failed (other BLE commands may still work)."
            )
        return None

    async def _read_ps_state_locked(
        self,
        client: BleakClient,
        *,
        timeout: float | None = None,
        pre_write_delay: float = 0.2,
    ) -> PSState | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_PS_STATE,
            parse_config_params_ps_state,
            "PS_State",
            timeout=timeout,
            pre_write_delay=pre_write_delay,
        )

    async def _read_clean_mode_locked(
        self,
        client: BleakClient,
        *,
        timeout: float | None = None,
        pre_write_delay: float = 0.2,
    ) -> CleanMode | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_WORKING_CLEAN_MODE,
            parse_config_params_clean_mode,
            "Working_Clean_Mode",
            timeout=timeout,
            pre_write_delay=pre_write_delay,
            warn_on_fail=False,
        )

    async def _read_cycle_time_locked(
        self,
        client: BleakClient,
    ) -> int | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_CYCLE_TIME,
            parse_config_params_cycle_time,
            "Cycle_Time",
            warn_on_fail=False,
        )

    async def _read_delay_time_locked(
        self,
        client: BleakClient,
    ) -> int | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_DELAY_TIME,
            parse_config_params_delay_time,
            "delay_time",
            warn_on_fail=False,
        )

    async def _read_weekly_program_locked(
        self,
        client: BleakClient,
    ) -> list[WeeklyProgramEntry] | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_WEEKLY_PROGRAM,
            parse_config_params_weekly_program,
            "Weekly_Program",
            warn_on_fail=False,
        )

    async def _read_weekly_repeat_locked(
        self,
        client: BleakClient,
    ) -> bool | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_REPEAT_WEEKLY_SCHEDULER,
            parse_config_params_weekly_repeat,
            "repeat_weekly_scheduler_flag",
            warn_on_fail=False,
        )

    async def _read_schedule_owner_locked(
        self,
        client: BleakClient,
    ) -> ScheduleOwner | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_OWNER_WEEKLY_TIMER,
            parse_config_params_schedule_owner,
            "Owner_of_Weekly_Timer_and_Delay",
            warn_on_fail=False,
        )

    async def _read_features_locked(
        self,
        client: BleakClient,
    ) -> dict[str, bool] | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_FEATURE_ENABLE,
            parse_config_params_features,
            "Feature_Enable_OR_Disable",
            warn_on_fail=False,
        )

    async def _read_mmi_delay_selection_locked(
        self,
        client: BleakClient,
    ) -> MMIDelaySelection | None:
        return await self._read_config_params_locked(
            client,
            CONFIG_PARAMS_CMD_MMI_DELAY_SELECTION,
            parse_config_params_mmi_delay_selection,
            "MMI_delay_selection",
            warn_on_fail=False,
        )

    async def _read_gatt_notify_locked(
        self,
        client: BleakClient,
        notify_uuid: str,
        write_uuid: str,
        payload: bytes,
        parser: Callable[[bytes], _T | None],
        log_label: str,
        *,
        timeout: float | None = None,
        pre_write_delay: float = 0.2,
    ) -> _T | None:
        per = timeout if timeout is not None else _PS_READ_PER_STRATEGY_TIMEOUT
        strategies = (
            (notify_uuid, notify_uuid, True),
            (notify_uuid, CONFIG_PARAMS_WRITE_UUID, True),
            (notify_uuid, notify_uuid, False),
        )
        for notify_u, write_u, rsp in strategies:
            try:
                got = await self._read_config_params_notify_once(
                    client,
                    notify_u,
                    write_u,
                    payload,
                    parser,
                    timeout=per,
                    pre_write_delay=pre_write_delay,
                    write_with_response=rsp,
                )
                if got is not None:
                    _LOGGER.debug("%s read ok (notify=%s write=%s)", log_label, notify_u, write_u)
                    return got
            except BleakError as err:
                _LOGGER.debug("%s BLE error: %s", log_label, err, exc_info=True)
                return None
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("%s failed: %s", log_label, err, exc_info=True)
        return None

    async def _read_internal_params_locked(
        self,
        client: BleakClient,
    ) -> InternalParamsSnapshot | None:
        snap = await self._read_gatt_notify_locked(
            client,
            INTERNAL_PARAMS_READ_UUID,
            INTERNAL_PARAMS_READ_UUID,
            build_internal_params_read_request(),
            parse_internal_params_snapshot,
            "InternalParamsRead",
        )
        if snap:
            _LOGGER.debug(
                "InternalParams: clean=%s climb=%s phase=%s motor=%s",
                snap.clean_mode_byte,
                snap.climb_every,
                snap.phase_byte,
                snap.motor_aux,
            )
        return snap

    async def _read_get_status_locked(
        self, client: BleakClient
    ) -> WorkingStatus | None:
        working = await self._read_gatt_notify_locked(
            client,
            GET_STATUS_READ_UUID,
            GET_STATUS_READ_UUID,
            build_get_status_read_request(),
            parse_get_status_working,
            "GetStatusRead",
        )
        if working is not None:
            return working
        try:
            raw = await asyncio.wait_for(
                client.read_gatt_char(GET_STATUS_READ_UUID),
                timeout=_GATT_READ_PROBE_TIMEOUT,
            )
            if raw:
                return parse_get_status_working(bytes(raw))
        except (BleakError, asyncio.TimeoutError):
            _LOGGER.debug("GetStatusRead plain GATT read failed", exc_info=True)
        return None

    async def async_poll_robot_state(
        self,
    ) -> tuple[
        PSState | None,
        CleanMode | None,
        CleaningSurface | None,
        WorkingStatus | None,
        InternalParamsSnapshot | None,
        bytes | None,
        bytes | None,
    ]:
        include_probe = bool(self._options().get(OPT_DIAGNOSTIC_PROBE, False))
        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
            except (BleakError, HomeAssistantError) as err:
                _LOGGER.debug("poll: could not connect: %s", err)
                await self._release_after_operation_locked(force=True)
                return (None, None, None, None, None, None, None)
            try:
                ps = await self._read_ps_state_locked(client)
                clean_mode = await self._read_clean_mode_locked(client)
                internal: InternalParamsSnapshot | None = None
                working: WorkingStatus | None = None
                if ps is not None and ps != PSState.OFF:
                    internal = await self._read_internal_params_locked(client)
                    gatt_working = await self._read_get_status_locked(client)
                    working = resolve_working_status(ps, gatt_working, internal)
                else:
                    working = None
                surface = infer_cleaning_surface(
                    ps, clean_mode, internal, working=working
                )
                ffc, ffd = (None, None)
                if include_probe:
                    ffc, ffd = await self._read_status_probe_locked(client)
                return (ps, clean_mode, surface, working, internal, ffc, ffd)
            except BleakError:
                await self._release_after_operation_locked(force=True)
                raise
            finally:
                await self._release_after_operation_locked()

    async def async_read_ps_state(
        self,
        *,
        timeout: float | None = None,
        pre_write_delay: float = 0.2,
    ) -> PSState | None:
        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
            except (BleakError, HomeAssistantError) as err:
                _LOGGER.debug("PS_State read: could not connect: %s", err)
                await self._release_after_operation_locked(force=True)
                return None
            try:
                return await self._read_ps_state_locked(
                    client, timeout=timeout, pre_write_delay=pre_write_delay
                )
            except BleakError:
                await self._release_after_operation_locked(force=True)
                raise
            finally:
                await self._release_after_operation_locked()

    async def async_read_status_probe(self) -> tuple[bytes | None, bytes | None]:
        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
            except (BleakError, HomeAssistantError):
                return (None, None)
            try:
                return await self._read_status_probe_locked(client)
            except BleakError:
                await self._release_after_operation_locked(force=True)
                raise
            finally:
                await self._release_after_operation_locked()

    async def async_read_native_schedule(self) -> dict[str, Any]:
        """Read native MyDolphin timer settings without writing to the robot."""
        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
            except (BleakError, HomeAssistantError) as err:
                _LOGGER.debug("native schedule read: could not connect: %s", err)
                await self._release_after_operation_locked(force=True)
                return {
                    "native_schedule_poll_ok": False,
                    "weekly_program": None,
                    "weekly_repeat": None,
                    "schedule_owner": None,
                    "cycle_time_minutes": None,
                    "delay_time_minutes": None,
                    "native_features": None,
                    "mmi_delay_selection": None,
                }
            try:
                owner = await self._read_schedule_owner_locked(client)
                weekly = await self._read_weekly_program_locked(client)
                repeat = await self._read_weekly_repeat_locked(client)
                cycle = await self._read_cycle_time_locked(client)
                delay = await self._read_delay_time_locked(client)
                features = await self._read_features_locked(client)
                mmi_delay = await self._read_mmi_delay_selection_locked(client)
                return {
                    "native_schedule_poll_ok": any(
                        value is not None
                        for value in (
                            owner,
                            weekly,
                            repeat,
                            cycle,
                            delay,
                            features,
                            mmi_delay,
                        )
                    ),
                    "weekly_program": weekly,
                    "weekly_repeat": repeat,
                    "schedule_owner": owner,
                    "cycle_time_minutes": cycle,
                    "delay_time_minutes": delay,
                    "native_features": features,
                    "mmi_delay_selection": mmi_delay,
                }
            except BleakError:
                await self._release_after_operation_locked(force=True)
                raise
            finally:
                await self._release_after_operation_locked()

    async def _write_native_config_params(
        self,
        command_code: int,
        payload: bytes,
        log_label: str,
    ) -> None:
        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
                ok = await self._write_config_params_locked(
                    client,
                    command_code,
                    payload,
                    log_label,
                )
                if not ok:
                    raise HomeAssistantError(f"Dolphin did not acknowledge {log_label}")
            except BleakError as err:
                await self._release_after_operation_locked(force=True)
                raise HomeAssistantError(f"BLE error: {err}") from err
            finally:
                await self._release_after_operation_locked()

    async def async_sync_rtc(self) -> None:
        """Write the current Home Assistant day/hour/minute to the robot RTC."""
        await self._write_native_config_params(
            CONFIG_PARAMS_CMD_RTC,
            build_rtc_write_request(),
            "RTC",
        )

    async def async_set_weekly_program(
        self,
        entries: list[WeeklyProgramEntry],
        *,
        repeat: bool | None = None,
        sync_rtc: bool = True,
    ) -> None:
        """Write the native weekly program and optional repeat flag."""
        if sync_rtc:
            await self.async_sync_rtc()
        await self._write_native_config_params(
            CONFIG_PARAMS_CMD_WEEKLY_PROGRAM,
            build_weekly_program_write_request(entries),
            "Weekly_Program",
        )
        if repeat is not None:
            await self.async_set_weekly_repeat(repeat)

    async def async_clear_weekly_program(self) -> None:
        """Clear the native weekly program and disable repeat."""
        await self.async_set_weekly_program([], repeat=False, sync_rtc=True)

    async def async_set_weekly_repeat(self, enabled: bool) -> None:
        """Write the native weekly repeat flag."""
        await self._write_native_config_params(
            CONFIG_PARAMS_CMD_REPEAT_WEEKLY_SCHEDULER,
            build_weekly_repeat_write_request(enabled),
            "repeat_weekly_scheduler_flag",
        )

    async def async_set_cycle_time(self, minutes: int) -> None:
        """Write native cycle time in minutes."""
        await self._write_native_config_params(
            CONFIG_PARAMS_CMD_CYCLE_TIME,
            build_cycle_time_write_request(minutes),
            "cycle_time",
        )

    async def async_set_delay_time(self, minutes: int) -> None:
        """Write native delayed-start time in minutes."""
        await self._write_native_config_params(
            CONFIG_PARAMS_CMD_DELAY_TIME,
            build_delay_time_write_request(minutes),
            "delay_mode_time",
        )

    async def async_enable_power_supply_features(self) -> None:
        """Enable app-supported power-supply features."""
        await self._write_native_config_params(
            CONFIG_PARAMS_CMD_FEATURE_ENABLE,
            build_features_write_request(
                filter_status=True,
                weekly_timer=True,
                delayed_start=True,
                speed=True,
            ),
            "Feature_Enable_OR_Disable",
        )

    async def async_set_clean_mode(self, option: str) -> bool:
        """Set selected cleaning mode using MyDolphin ``ConfigParamsWrite`` packets."""
        try:
            key = clean_mode_key(option)
            command_code, payload = build_clean_mode_write_request(key)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

        async with self._lock:
            try:
                client = await self._ensure_connected_locked()
                ok = await self._write_config_params_locked(
                    client,
                    command_code,
                    payload,
                    f"Set clean mode {key}",
                )
                if not ok:
                    raise HomeAssistantError(
                        f"Dolphin did not acknowledge clean mode {key}"
                    )
                return True
            except BleakError as err:
                await self._release_after_operation_locked(force=True)
                raise HomeAssistantError(f"BLE error: {err}") from err
            finally:
                await self._release_after_operation_locked()

    async def async_reconnect(self) -> None:
        """Release BLE (same as app disconnecting) — does not open a new session."""
        if self._shutting_down:
            self._shutting_down = False
        await self.async_release_ble_link()


async def async_ble_periodic_release(hass: HomeAssistant, entry_id: str) -> None:
    """Periodically disconnect so HA never holds the robot hostage."""
    try:
        while True:
            entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
            session: DolphinBleConnection | None = (
                entry_data.get(DATA_BLE_SESSION) if entry_data else None
            )
            if session is None or session._shutting_down:
                return
            entry = hass.config_entries.async_get_entry(entry_id)
            opts = get_integration_options(entry) if entry else {}
            if opts.get(OPT_BLE_PERSISTENT_SESSION):
                await asyncio.sleep(60)
                continue
            interval = int(opts.get(OPT_BLE_KEEPALIVE_SEC, 0))
            if interval <= 0:
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(interval)
            if session.is_connected:
                _LOGGER.debug(
                    "Maytronics Dolphin: periodic BLE release (%ss)", interval
                )
            await session.async_release_ble_link()
    except asyncio.CancelledError:
        raise
