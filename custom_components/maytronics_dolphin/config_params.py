"""ConfigParamsRead / Write wire helpers (MyDolphin ``DolphinData`` on ``fff0`` service).

Characteristic UUIDs (swap‑fixed in ``const.py``): ``ConfigParamsRead`` → ``fffa``,
``ConfigParamsWrite`` → ``fff9``.

MyDolphin 2.3.19 (``classes2.dex``): ``ConfigParamsRead.getBytes()`` allocates **3**
bytes, fills zeros, sets ``buf[0]=SOP``, ``buf[1]=CommandType.CODE``, then
``DolphinData.updateCRC`` (CRC over ``length-1`` bytes → last byte). The read **ACK**
payload length from ``getAckDataLength()`` is **47** bytes — that is the notify
payload, not the outgoing write length.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from .const import SOP
from .protocol import build_short_frame, crc_run

_LOGGER = logging.getLogger(__name__)

# ``ConfigParamsRead$CommandType`` wire ``CODE`` bytes (APK 2.3.19 ``classes2.dex``).
CONFIG_PARAMS_CMD_PS_STATE = 13
CONFIG_PARAMS_CMD_WORKING_CLEAN_MODE = 5
CONFIG_PARAMS_CMD_CLIMBING_WALL_TIME = 2
CONFIG_PARAMS_CMD_CYCLE_TIME = 1
CONFIG_PARAMS_CMD_DELAY_TIME = 3
CONFIG_PARAMS_CMD_WEEKLY_PROGRAM = 4
CONFIG_PARAMS_CMD_REPEAT_WEEKLY_SCHEDULER = 6
CONFIG_PARAMS_CMD_RTC = 9
CONFIG_PARAMS_CMD_FEATURE_ENABLE = 11
CONFIG_PARAMS_CMD_OWNER_WEEKLY_TIMER = 14
CONFIG_PARAMS_CMD_MMI_DELAY_SELECTION = 15
CONFIG_PARAMS_WRITE_FRAME_LEN = 46
CONFIG_PARAMS_WRITE_ACK_LEN = 4
FLOOR_ONLY_CLIMB_MARKER = 234

WEEKLY_DAY_LABELS: tuple[str, ...] = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)


@dataclass(frozen=True)
class WeeklyProgramEntry:
    """One native weekly schedule entry from MyDolphin ``WeeklyTimerData``."""

    day: int
    hour: int
    minute: int

    @property
    def day_name(self) -> str:
        try:
            return WEEKLY_DAY_LABELS[self.day]
        except IndexError:
            return f"jour_{self.day}"

    @property
    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


class ScheduleOwner(IntEnum):
    """``Owner_of_Weekly_Timer_and_Delay``: MMI is the power-supply buttons."""

    MMI = 0
    RA = 1


class MMIDelaySelection(IntEnum):
    """``MMI_delay_selection``: delayed-start selection from power-supply buttons."""

    NONE = 0
    SHORT = 1
    LONG = 2


class PSState(IntEnum):
    """``ConfigParamsRead.getAck`` PS_State branch (byte after SOP, cmd, err)."""

    OFF = 0
    ON = 1
    HOLD = 2
    PROGRAMMING = 3
    BIST = 4


class CleanMode(IntEnum):
    """``ConfigParamsRead$CleanMode`` wire ``CODE`` (``CleanMode.get(B)`` matches on CODE)."""

    REGULAR = 1
    ULTRACLEAN = 2
    SWIMMER = 3
    WATERLINE = 4
    FAST_MODE = 5
    LINE_TO = 6
    DYNAMIC_FAST_CLEAN = 7
    TIC_TAC = 0x98  # APK ``tic_tac`` uses signed byte **-104**


def build_config_params_read_request(command_code: int = CONFIG_PARAMS_CMD_PS_STATE) -> bytes:
    """Same on-air layout as APK ``ConfigParamsRead.getBytes()`` — ``[SOP, cmd, crc]``."""
    return build_short_frame(int(command_code) & 0xFF)


def build_config_params_write_request(command_code: int, value: int) -> bytes:
    """Same on-air layout as APK ``ConfigParamsWrite.getBytes()`` for one int arg."""
    buf = bytearray(CONFIG_PARAMS_WRITE_FRAME_LEN)
    buf[0] = SOP & 0xFF
    buf[1] = int(command_code) & 0xFF
    arg = int(value) & 0xFFFFFFFF
    # APK ``toBytes(value)`` is big-endian, then ConfigParamsWrite stores it reversed.
    buf[2] = arg & 0xFF
    buf[3] = (arg >> 8) & 0xFF
    buf[4] = (arg >> 16) & 0xFF
    buf[5] = (arg >> 24) & 0xFF
    buf[-1] = crc_run(bytes(buf[:-1]), len(buf) - 1)
    return bytes(buf)


def build_config_params_write_bytes_request(
    command_code: int,
    values: list[int] | tuple[int, ...],
    *,
    terminator: bool = False,
) -> bytes:
    """Build APK-style byte-arg writes (Weekly_Program / RTC / feature flags)."""
    buf = bytearray(CONFIG_PARAMS_WRITE_FRAME_LEN)
    buf[0] = SOP & 0xFF
    buf[1] = int(command_code) & 0xFF
    for idx, value in enumerate(values[:43]):
        buf[2 + idx] = int(value) & 0xFF
    if terminator and len(values) < 43:
        buf[2 + len(values)] = 0xFF
    buf[-1] = crc_run(bytes(buf[:-1]), len(buf) - 1)
    return bytes(buf)


def build_weekly_program_write_request(
    entries: list[WeeklyProgramEntry] | tuple[WeeklyProgramEntry, ...],
) -> bytes:
    """Build MyDolphin ``Weekly_Program`` write: triples plus 0xff terminator."""
    values: list[int] = []
    for item in sorted(entries, key=lambda entry: entry.day):
        if item.day < 0 or item.day > 6:
            raise ValueError(f"Invalid weekly day: {item.day}")
        if item.hour < 0 or item.hour > 23:
            raise ValueError(f"Invalid weekly hour: {item.hour}")
        if item.minute < 0 or item.minute > 59:
            raise ValueError(f"Invalid weekly minute: {item.minute}")
        values.extend([item.day, item.hour, item.minute])
    if len(values) > 42:
        raise ValueError("Too many weekly timer entries")
    return build_config_params_write_bytes_request(
        CONFIG_PARAMS_CMD_WEEKLY_PROGRAM,
        values,
        terminator=True,
    )


def build_weekly_repeat_write_request(enabled: bool) -> bytes:
    """Build ``repeat_weekly_scheduler_flag`` write."""
    return build_config_params_write_request(
        CONFIG_PARAMS_CMD_REPEAT_WEEKLY_SCHEDULER,
        1 if enabled else 0,
    )


def build_cycle_time_write_request(minutes: int) -> bytes:
    """Build ``cycle_time`` write; APK stores the value in 6-minute units."""
    if minutes <= 0:
        raise ValueError("Cycle time must be positive")
    return build_config_params_write_request(
        CONFIG_PARAMS_CMD_CYCLE_TIME,
        max(1, round(minutes / 6)),
    )


def build_delay_time_write_request(minutes: int) -> bytes:
    """Build ``delay_mode_time`` write."""
    if minutes < 0:
        raise ValueError("Delay time cannot be negative")
    return build_config_params_write_request(CONFIG_PARAMS_CMD_DELAY_TIME, minutes)


def calendar_to_device_day(weekday: int) -> int:
    """Convert Python Monday=0..Sunday=6 to Dolphin Monday=0..Sunday=6."""
    return int(weekday) % 7


def build_rtc_write_request(now: datetime | None = None) -> bytes:
    """Build MyDolphin ``RTC`` write: ``[device_day, hour, minute]``."""
    stamp = now or datetime.now().astimezone()
    return build_config_params_write_bytes_request(
        CONFIG_PARAMS_CMD_RTC,
        [calendar_to_device_day(stamp.weekday()), stamp.hour, stamp.minute],
    )


def build_features_write_request(
    *,
    filter_status: bool = True,
    weekly_timer: bool = True,
    delayed_start: bool = True,
    speed: bool = True,
) -> bytes:
    """Build APK-style ``Feature_Enable_OR_Disable`` write."""
    flags = 0
    if filter_status:
        flags |= 0x01
    if weekly_timer:
        flags |= 0x02
    if delayed_start:
        flags |= 0x04
    if speed:
        flags |= 0x08
    return build_config_params_write_bytes_request(
        CONFIG_PARAMS_CMD_FEATURE_ENABLE,
        [0, flags, 0, 0],
    )


def build_clean_mode_write_request(option: str) -> tuple[int, bytes]:
    """Build the APK-equivalent write packet for a clean-mode option key."""
    key = clean_mode_key(option)
    if key == "floor_only":
        return (
            CONFIG_PARAMS_CMD_CLIMBING_WALL_TIME,
            build_config_params_write_request(
                CONFIG_PARAMS_CMD_CLIMBING_WALL_TIME,
                FLOOR_ONLY_CLIMB_MARKER,
            ),
        )
    mode = clean_mode_from_key(key)
    if mode is None:
        raise ValueError(f"Unsupported clean mode: {option}")
    return (
        CONFIG_PARAMS_CMD_WORKING_CLEAN_MODE,
        build_config_params_write_request(
            CONFIG_PARAMS_CMD_WORKING_CLEAN_MODE,
            int(mode),
        ),
    )


def _crc_ok_47(frame: bytes, start: int) -> bool:
    if start + 47 > len(frame):
        return False
    chunk = frame[start : start + 47]
    return chunk[46] == crc_run(chunk[:46], 46)


def _parse_config_params_ack_byte(data: bytes, command_code: int) -> int | None:
    """Port of ``ConfigParamsRead.getAck``: byte at offset 3 when ``[SOP,cmd,err==0]``."""
    payload = _parse_config_params_ack_payload(data, command_code)
    if payload is None or not payload:
        return None
    value = payload[0]
    if value == 0xFF:
        return None
    return value


def _parse_config_params_ack_payload(
    data: bytes, command_code: int
) -> bytes | None:
    """Return the 43-byte ConfigParamsRead ACK payload after ``[SOP,cmd,err]``."""
    if not data:
        return None
    i = 0
    while i < len(data):
        if data[i] != SOP:
            i += 1
            continue
        if i + 3 >= len(data):
            return None
        if data[i + 1] != (int(command_code) & 0xFF):
            i += 1
            continue
        if data[i + 2] != 0:
            return None
        if i + 47 <= len(data) and not _crc_ok_47(data, i):
            _LOGGER.debug(
                "ConfigParamsRead cmd %s CRC mismatch; frame=%s",
                command_code,
                data[i : i + 47].hex(),
            )
        end = min(i + 46, len(data))
        return data[i + 3 : end]
    return None


def parse_config_params_ps_state(data: bytes) -> PSState | None:
    """Minimal port of ``ConfigParamsRead.getAck`` for ``PS_State`` (wire cmd **13**)."""
    ps_byte = _parse_config_params_ack_byte(data, CONFIG_PARAMS_CMD_PS_STATE)
    if ps_byte is None:
        return None
    try:
        return PSState(ps_byte)
    except ValueError:
        return None


def parse_config_params_clean_mode(data: bytes) -> CleanMode | None:
    """``ConfigParamsRead.getAck`` → ``CleanMode.get(B)`` for ``Working_Clean_Mode`` (cmd **5**)."""
    mode_byte = _parse_config_params_ack_byte(data, CONFIG_PARAMS_CMD_WORKING_CLEAN_MODE)
    if mode_byte is None:
        return None
    try:
        return CleanMode(mode_byte)
    except ValueError:
        return None


def parse_config_params_cycle_time(data: bytes) -> int | None:
    """Return cycle time in minutes (APK stores 6-minute units)."""
    value = _parse_config_params_ack_byte(data, CONFIG_PARAMS_CMD_CYCLE_TIME)
    if value is None:
        return None
    return value * 6


def parse_config_params_delay_time(data: bytes) -> int | None:
    """Return native delayed-start value from ``delay_time`` (little-endian minutes)."""
    payload = _parse_config_params_ack_payload(data, CONFIG_PARAMS_CMD_DELAY_TIME)
    if payload is None or len(payload) < 2:
        return None
    if payload[0] == 0xFF:
        return 0
    return int(payload[0]) | (int(payload[1]) << 8)


def parse_config_params_weekly_program(data: bytes) -> list[WeeklyProgramEntry] | None:
    """Parse native weekly timer triples: ``[device_day, hour, minute]...0xff``."""
    payload = _parse_config_params_ack_payload(data, CONFIG_PARAMS_CMD_WEEKLY_PROGRAM)
    if payload is None:
        return None
    entries: list[WeeklyProgramEntry] = []
    idx = 0
    while idx < len(payload):
        day = payload[idx]
        if day == 0xFF:
            return entries
        if idx + 2 >= len(payload):
            return None
        hour = payload[idx + 1]
        minute = payload[idx + 2]
        if day > 6 or hour > 23 or minute > 59:
            return None
        entries.append(WeeklyProgramEntry(day=day, hour=hour, minute=minute))
        idx += 3
    return entries


def parse_config_params_weekly_repeat(data: bytes) -> bool | None:
    """Return whether the native weekly schedule repeats."""
    payload = _parse_config_params_ack_payload(
        data, CONFIG_PARAMS_CMD_REPEAT_WEEKLY_SCHEDULER
    )
    if payload is None or not payload:
        return None
    if payload[0] == 0xFF:
        return False
    return payload[0] == 1


def parse_config_params_schedule_owner(data: bytes) -> ScheduleOwner | None:
    """Parse schedule owner: ``MMI`` physical PSU buttons, ``RA`` app/remote."""
    owner_byte = _parse_config_params_ack_byte(
        data, CONFIG_PARAMS_CMD_OWNER_WEEKLY_TIMER
    )
    if owner_byte is None:
        return None
    try:
        return ScheduleOwner(owner_byte)
    except ValueError:
        return None


def parse_config_params_features(data: bytes) -> dict[str, bool] | None:
    """Parse ``Feature_Enable_OR_Disable`` flags from ConfigParamsRead cmd 11."""
    payload = _parse_config_params_ack_payload(data, CONFIG_PARAMS_CMD_FEATURE_ENABLE)
    if payload is None or len(payload) < 2 or payload[0] == 0xFF:
        return None
    flags = payload[1]
    return {
        "filter_status": bool(flags & 0x01),
        "weekly_timer": bool(flags & 0x02),
        "delayed_start": bool(flags & 0x04),
        "speed": bool(flags & 0x08),
    }


def parse_config_params_mmi_delay_selection(
    data: bytes,
) -> MMIDelaySelection | None:
    """Parse physical power-supply delayed-start selection."""
    value = _parse_config_params_ack_byte(data, CONFIG_PARAMS_CMD_MMI_DELAY_SELECTION)
    if value is None:
        return None
    try:
        return MMIDelaySelection(value)
    except ValueError:
        return None


def parse_config_params_write_ack(data: bytes, command_code: int) -> bool | None:
    """Parse 4-byte ``ConfigParamsWrite`` ACK: ``[SOP, cmd, err, crc]``."""
    if not data:
        return None
    i = 0
    while i < len(data):
        if data[i] != SOP:
            i += 1
            continue
        if i + CONFIG_PARAMS_WRITE_ACK_LEN > len(data):
            return None
        if data[i + 1] != (int(command_code) & 0xFF):
            i += 1
            continue
        return data[i + 2] == 0
    return None


def weekly_program_to_attr(
    entries: list[WeeklyProgramEntry] | None,
) -> dict[str, str | None]:
    """Expose weekly entries as stable French day attributes."""
    attrs: dict[str, str | None] = {day: None for day in WEEKLY_DAY_LABELS}
    if entries is None:
        return attrs
    for item in entries:
        attrs[item.day_name] = item.time_str
    return attrs


def weekly_program_to_str(entries: list[WeeklyProgramEntry] | None) -> str:
    """Compact user-facing summary for a native weekly timer."""
    if entries is None:
        return "unknown"
    if not entries:
        return "aucun"
    return ", ".join(f"{item.day_name} {item.time_str}" for item in entries)


def schedule_owner_to_str(owner: ScheduleOwner | None) -> str:
    """User-facing owner label."""
    if owner is None:
        return "unknown"
    if owner == ScheduleOwner.MMI:
        return "boitier"
    if owner == ScheduleOwner.RA:
        return "application"
    return f"unknown_code_{int(owner)}"


def features_to_str(features: dict[str, bool] | None) -> str:
    """Compact feature summary."""
    if features is None:
        return "unknown"
    enabled = [name for name, active in features.items() if active]
    if not enabled:
        return "aucune"
    return ", ".join(enabled)


def mmi_delay_selection_to_str(selection: MMIDelaySelection | None) -> str:
    """User-facing MMI delay label."""
    if selection is None:
        return "unknown"
    labels = {
        MMIDelaySelection.NONE: "aucun",
        MMIDelaySelection.SHORT: "court",
        MMIDelaySelection.LONG: "long",
    }
    return labels.get(selection, f"unknown_code_{int(selection)}")


def ps_state_implies_power_on(state: PSState | None) -> bool | None:
    """HA Power switch: off only when robot reports OFF."""
    if state is None:
        return None
    if state == PSState.OFF:
        return False
    return True


PS_STATE_LABEL: dict[PSState, str] = {
    PSState.OFF: "off",
    PSState.ON: "on",
    PSState.HOLD: "hold",
    PSState.PROGRAMMING: "programming",
    PSState.BIST: "self_test",
}


def ps_state_to_str(state: PSState | None) -> str:
    """Human-readable cleaner state for ``sensor`` entities."""
    if state is None:
        return "unknown"
    return PS_STATE_LABEL.get(state, f"unknown_code_{int(state)}")


def ps_state_cleaning_active(state: PSState | None) -> bool | None:
    """True when robot is not fully off (includes hold / programming / BIST)."""
    if state is None:
        return None
    return state != PSState.OFF


CLEAN_MODE_LABEL: dict[CleanMode, str] = {
    CleanMode.REGULAR: "regular",
    CleanMode.ULTRACLEAN: "ultraclean",
    CleanMode.SWIMMER: "swimmer",
    CleanMode.WATERLINE: "waterline",
    CleanMode.FAST_MODE: "fast_mode",
    CleanMode.LINE_TO: "line_to",
    CleanMode.DYNAMIC_FAST_CLEAN: "dynamic_fast_clean",
    CleanMode.TIC_TAC: "tic_tac",
}

CLEAN_MODE_KEY_TO_MODE: dict[str, CleanMode] = {
    "regular": CleanMode.REGULAR,
    "fast_mode": CleanMode.FAST_MODE,
    "floor_only": CleanMode.REGULAR,
    "waterline": CleanMode.WATERLINE,
    "ultraclean": CleanMode.ULTRACLEAN,
}

CLEAN_MODE_LABEL_TO_KEY: dict[str, str] = {
    "Standard": "regular",
    "Rapide": "fast_mode",
    "Fond seul": "floor_only",
    "Ligne d'eau": "waterline",
    "Ultra": "ultraclean",
}

CLEAN_MODE_KEY_TO_LABEL: dict[str, str] = {
    key: label for label, key in CLEAN_MODE_LABEL_TO_KEY.items()
}

CLEAN_MODE_SELECT_OPTIONS: list[str] = list(CLEAN_MODE_LABEL_TO_KEY)


def clean_mode_key(option: str) -> str:
    """Normalize a UI label or internal key to an internal clean-mode key."""
    if option in CLEAN_MODE_LABEL_TO_KEY:
        return CLEAN_MODE_LABEL_TO_KEY[option]
    key = option.strip().lower().replace(" ", "_")
    if key in CLEAN_MODE_KEY_TO_MODE:
        return key
    raise ValueError(f"Unsupported clean mode: {option}")


def clean_mode_from_key(key: str) -> CleanMode | None:
    """Return the wire clean mode for an internal option key."""
    return CLEAN_MODE_KEY_TO_MODE.get(clean_mode_key(key))


def clean_mode_label_from_key(key: str | None) -> str | None:
    """Return the French option label for an internal clean-mode key."""
    if key is None:
        return None
    return CLEAN_MODE_KEY_TO_LABEL.get(key)


def clean_mode_to_str(mode: CleanMode | None) -> str:
    """Human-readable clean program for sensor entities."""
    if mode is None:
        return "unknown"
    return CLEAN_MODE_LABEL.get(mode, f"unknown_code_{int(mode)}")
