"""Единый событийный слой Intersvyaz."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    DOOR_EVENT_FACE_RECOGNIZED,
    DOOR_EVENT_OPENED,
    DOOR_EVENT_OPEN_FAILED,
    DOOR_EVENT_UNKNOWN_PERSON,
    EVENT_DOOR_OPENED,
    EVENT_DOOR_OPEN_FAILED,
    EVENT_FACE_RECOGNIZED,
    EVENT_UNKNOWN_PERSON,
    SIGNAL_DOOR_EVENT,
)
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.events")

_BUS_EVENT_BY_TYPE = {
    DOOR_EVENT_FACE_RECOGNIZED: EVENT_FACE_RECOGNIZED,
    DOOR_EVENT_UNKNOWN_PERSON: EVENT_UNKNOWN_PERSON,
    DOOR_EVENT_OPENED: EVENT_DOOR_OPENED,
    DOOR_EVENT_OPEN_FAILED: EVENT_DOOR_OPEN_FAILED,
}


def _door_metadata(entry: IntersvyazConfigEntry, source_uid: str) -> dict[str, Any]:
    door = next((item for item in entry.runtime_data.doors if item.uid == source_uid), None)
    if door is not None:
        return {
            "door_uid": door.uid,
            "door_name": door.address or "Домофон",
            "is_main": door.is_main,
            "is_shared": door.is_shared,
            "source_type": "door",
        }

    camera = entry.runtime_data.yard_camera_manager.get(source_uid)
    if camera is not None:
        return {
            "door_uid": camera.uid,
            "door_name": camera.address or camera.name or "Камера Интерсвязи",
            "is_main": False,
            "is_shared": True,
            "source_type": "yard_camera",
            "porch": camera.porch,
        }
    return {"door_uid": source_uid, "source_type": "unknown"}


def emit_door_event(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    door_uid: str,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Опубликовать событие домофона/камеры во все стандартные каналы HA."""

    payload = {
        "entry_id": entry.entry_id,
        **_door_metadata(entry, door_uid),
        **(data or {}),
    }

    if event_type in (DOOR_EVENT_FACE_RECOGNIZED, DOOR_EVENT_UNKNOWN_PERSON):
        entry.runtime_data.last_visitors[door_uid] = {
            "event_type": event_type,
            **payload,
        }

    bus_event = _BUS_EVENT_BY_TYPE.get(event_type)
    if bus_event:
        hass.bus.async_fire(bus_event, payload)

    async_dispatcher_send(
        hass,
        f"{SIGNAL_DOOR_EVENT}_{entry.entry_id}",
        door_uid,
        event_type,
        payload,
    )

    _LOGGER.info(
        "Door event: entry_id=%s source=%s type=%s person_present=%s",
        entry.entry_id,
        safe_door_ref(door_uid),
        event_type,
        bool(payload.get("person")),
    )


def face_payload(
    *,
    person: str | None,
    person_entity_id: str | None,
    distance: float | None,
    threshold: float,
    faces_detected: int,
    streak: int,
    required_matches: int,
) -> dict[str, Any]:
    rounded_distance = round(float(distance), 4) if distance is not None else None
    match_score = None
    if rounded_distance is not None:
        match_score = round(max(0.0, min(1.0, 1.0 - rounded_distance)), 4)
    return {
        "person": person,
        "person_entity_id": person_entity_id,
        "distance": rounded_distance,
        "match_score": match_score,
        "threshold": float(threshold),
        "faces_detected": int(faces_detected),
        "streak": int(streak),
        "required_matches": int(required_matches),
    }
