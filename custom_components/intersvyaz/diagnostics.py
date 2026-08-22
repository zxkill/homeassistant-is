"""Безопасная диагностика интеграции Intersvyaz."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BACKGROUND_CAMERAS,
    CONF_KNOWN_FACES,
    DATA_DOOR_OPENERS,
    DATA_FACE_MANAGER,
    DOMAIN,
)

_REDACTED = "**REDACTED**"
_SENSITIVE_KEYS = {
    "token",
    "mobile_token",
    "crm_token",
    "authorization",
    "phone",
    "phone_number",
    "device_id",
    "unique_device_id",
    "mac",
    "door_mac",
    "address",
    "door_address",
    "open_link",
    "door_open_link",
    "image_url",
    "door_image_url",
    "relay_payload",
    "face_encoding",
}


def _redact(value: Any, *, key: str = "") -> Any:
    """Рекурсивно удалить секреты и персональные данные из диагностики."""

    normalized_key = key.lower()
    if normalized_key in _SENSITIVE_KEYS or normalized_key.endswith("token"):
        return _REDACTED
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key=key) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Вернуть диагностические данные без токенов, адресов и биометрии."""

    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    doors = runtime.get(DATA_DOOR_OPENERS, []) or []
    manager = runtime.get(DATA_FACE_MANAGER)

    door_summary = []
    for door in doors:
        if not isinstance(door, dict):
            continue
        door_summary.append(
            {
                "uid": _REDACTED,
                "is_main": bool(door.get("is_main")),
                "is_shared": bool(door.get("is_shared")),
                "has_video": bool(door.get("has_video")),
                "has_open_link": bool(door.get("open_link")),
                "has_image_url": bool(door.get("image_url")),
                "has_callback": callable(door.get("callback")),
            }
        )

    known_faces_count = 0
    engine_available = None
    if manager is not None:
        try:
            known_faces_count = len(manager.list_known_face_names())
        except Exception:
            known_faces_count = -1
        engine_available = bool(getattr(manager, "library_available", False))

    options = dict(entry.options)
    options.pop(CONF_KNOWN_FACES, None)

    return {
        "entry": {
            "title": _REDACTED,
            "data": _redact(dict(entry.data)),
            "options": _redact(options),
        },
        "runtime": {
            "door_count": len(door_summary),
            "doors": door_summary,
            "background_camera_count": len(
                entry.options.get(CONF_BACKGROUND_CAMERAS, []) or []
            ),
            "known_faces_count": known_faces_count,
            "recognition_engine_available": engine_available,
        },
    }
