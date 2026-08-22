"""События интеграции Intersvyaz для автоматизаций Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN, EVENT_FACE_RECOGNIZED, EVENT_UNKNOWN_PERSON

_LOGGER = logging.getLogger(f"{DOMAIN}.events")


def fire_face_recognized(
    hass: HomeAssistant,
    *,
    door_uid: str,
    person: str,
    distance: float | None,
    threshold: float,
    faces_detected: int,
) -> None:
    """Сообщить Home Assistant об успешном распознавании лица."""

    payload: dict[str, Any] = {
        "door_uid": door_uid,
        "person": person,
        "distance": round(distance, 4) if distance is not None else None,
        "threshold": float(threshold),
        "faces_detected": int(faces_detected),
    }
    _LOGGER.info(
        "Событие распознавания лица: door_uid=%s person=%s distance=%s threshold=%s",
        door_uid,
        person,
        payload["distance"],
        threshold,
    )
    hass.bus.async_fire(EVENT_FACE_RECOGNIZED, payload)


def fire_unknown_person(
    hass: HomeAssistant,
    *,
    door_uid: str,
    distance: float | None,
    threshold: float,
    faces_detected: int,
) -> None:
    """Сообщить Home Assistant о лице без совпадения в локальной базе."""

    payload: dict[str, Any] = {
        "door_uid": door_uid,
        "distance": round(distance, 4) if distance is not None else None,
        "threshold": float(threshold),
        "faces_detected": int(faces_detected),
    }
    _LOGGER.debug(
        "Событие неизвестного посетителя: door_uid=%s faces=%s distance=%s",
        door_uid,
        faces_detected,
        payload["distance"],
    )
    hass.bus.async_fire(EVENT_UNKNOWN_PERSON, payload)
