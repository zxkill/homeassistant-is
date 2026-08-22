"""Безопасная диагностика интеграции Intersvyaz."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_BACKGROUND_CAMERAS, CONF_KNOWN_FACES
from .runtime import IntersvyazConfigEntry
from .security import REDACTED, redact_mapping

_LOGGER = logging.getLogger("custom_components.intersvyaz.diagnostics")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
) -> dict[str, Any]:
    """Вернуть диагностические данные без токенов, адресов и биометрии."""

    runtime = entry.runtime_data
    options = dict(entry.options)
    options.pop(CONF_KNOWN_FACES, None)

    doors = [
        {
            "uid": REDACTED,
            "is_main": door.is_main,
            "is_shared": door.is_shared,
            "has_video": door.has_video,
            "has_open_link": bool(door.open_link),
            "has_image_url": bool(door.image_url),
            "status_code": door.status_code,
            "callback_available": callable(door.callback),
        }
        for door in runtime.doors
    ]

    yard_cameras = [
        {
            "uid": REDACTED,
            "live_access": camera.live_access,
            "archive_access": camera.archive_access,
            "movement_access": camera.movement_access,
            "has_snapshot_url": bool(camera.snapshot_url),
            "has_hls_url": bool(camera.hls_url),
            "matched_to_door": bool(camera.matched_door_uid),
        }
        for camera in runtime.yard_cameras
    ]

    try:
        known_faces = runtime.face_manager.list_known_faces()
        known_faces_count = len(known_faces)
        linked_people_count = sum(1 for face in known_faces if face.person_entity_id)
        engine_available = bool(runtime.face_manager.library_available)
        recognition_mode = runtime.face_manager.recognition_mode
    except Exception:  # pragma: no cover - diagnostics must never break HA UI
        _LOGGER.exception("Не удалось собрать часть диагностики распознавания")
        known_faces_count = -1
        linked_people_count = -1
        engine_available = False
        recognition_mode = "unknown"

    result = {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "data": redact_mapping(dict(entry.data)),
            "options": redact_mapping(options),
        },
        "runtime": {
            "door_count": len(doors),
            "doors": doors,
            "yard_camera_count": len(yard_cameras),
            "yard_live_camera_count": sum(1 for item in yard_cameras if item["live_access"]),
            "yard_cameras": yard_cameras,
            "known_faces_count": known_faces_count,
            "linked_people_count": linked_people_count,
            "recognition_engine_available": engine_available,
            "recognition_mode": recognition_mode,
            "background_camera_count": len(
                entry.options.get(CONF_BACKGROUND_CAMERAS, []) or []
            ),
            "snapshot_cache_entries": runtime.snapshot_manager.cache_size,
        },
    }
    _LOGGER.debug(
        "Диагностика подготовлена: entry_id=%s doors=%s yard_cameras=%s faces=%s",
        entry.entry_id,
        len(doors),
        len(yard_cameras),
        known_faces_count,
    )
    return result
