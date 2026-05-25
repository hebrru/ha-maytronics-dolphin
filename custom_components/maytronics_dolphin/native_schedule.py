"""Editable native weekly schedule state for Home Assistant entities."""

from __future__ import annotations

from typing import Any

from .config_params import WEEKLY_DAY_LABELS, WeeklyProgramEntry


def default_native_schedule_editor() -> dict[str, Any]:
    """Return editor values used by schedule switches/numbers."""
    return {
        "repeat": False,
        "cycle_time_minutes": 120,
        "delay_time_minutes": 0,
        "days": [
            {"enabled": False, "hour": 9, "minute": 0}
            for _day in WEEKLY_DAY_LABELS
        ],
    }


def sync_editor_from_native_data(
    editor: dict[str, Any],
    data: dict[str, Any],
) -> None:
    """Copy a successful robot timer read into the editable HA controls."""
    entries: list[WeeklyProgramEntry] | None = data.get("weekly_program")
    if entries is not None:
        for day_cfg in editor["days"]:
            day_cfg["enabled"] = False
        for item in entries:
            if 0 <= item.day < len(editor["days"]):
                editor["days"][item.day]["enabled"] = True
                editor["days"][item.day]["hour"] = item.hour
                editor["days"][item.day]["minute"] = item.minute

    repeat = data.get("weekly_repeat")
    if repeat is not None:
        editor["repeat"] = bool(repeat)

    cycle = data.get("cycle_time_minutes")
    if cycle is not None:
        editor["cycle_time_minutes"] = int(cycle)

    delay = data.get("delay_time_minutes")
    if delay is not None:
        editor["delay_time_minutes"] = int(delay)


def entries_from_editor(editor: dict[str, Any]) -> list[WeeklyProgramEntry]:
    """Return native weekly entries from editor values."""
    entries: list[WeeklyProgramEntry] = []
    for day, day_cfg in enumerate(editor["days"]):
        if not day_cfg.get("enabled"):
            continue
        entries.append(
            WeeklyProgramEntry(
                day=day,
                hour=int(day_cfg.get("hour", 9)),
                minute=int(day_cfg.get("minute", 0)),
            )
        )
    return entries
