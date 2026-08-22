"""Actions/services интеграции Intersvyaz."""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any
from functools import partial
from urllib.parse import urlsplit

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    SERVICE_ADD_KNOWN_FACE,
    SERVICE_OPEN_DOOR,
    SERVICE_REMOVE_KNOWN_FACE,
    SNAPSHOT_MAX_BYTES,
)
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.services")

OPEN_DOOR_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Optional("door_uid"): cv.string,
    }
)

ADD_FACE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("image_url"): cv.string,
        vol.Optional("image_base64"): cv.string,
        vol.Optional("faces"): list,
    }
)

REMOVE_FACE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("name"): cv.string,
    }
)


def _get_entry(hass: HomeAssistant, entry_id: str) -> IntersvyazConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
        )
    if entry.state != ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
        )
    try:
        entry.runtime_data
    except AttributeError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="runtime_not_ready",
        ) from err
    return entry  # type: ignore[return-value]


async def async_setup_services(hass: HomeAssistant) -> None:
    """Зарегистрировать глобальные actions один раз."""

    if not hass.services.has_service(DOMAIN, SERVICE_OPEN_DOOR):
        hass.services.async_register(
            DOMAIN, SERVICE_OPEN_DOOR, partial(_handle_open_door, hass), schema=OPEN_DOOR_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_ADD_KNOWN_FACE):
        hass.services.async_register(
            DOMAIN, SERVICE_ADD_KNOWN_FACE, partial(_handle_add_face, hass), schema=ADD_FACE_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_REMOVE_KNOWN_FACE):
        hass.services.async_register(
            DOMAIN, SERVICE_REMOVE_KNOWN_FACE, partial(_handle_remove_face, hass), schema=REMOVE_FACE_SCHEMA
        )
    _LOGGER.debug("Actions Intersvyaz зарегистрированы")


async def _handle_open_door(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _get_entry(hass, str(call.data["entry_id"]))
    requested_uid = call.data.get("door_uid")
    door = (
        entry.runtime_data.door_manager.get(str(requested_uid))
        if requested_uid
        else entry.runtime_data.default_door
    )
    if door is None or not callable(door.callback):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="door_not_found",
        )
    _LOGGER.info(
        "Action open_door: entry_id=%s door=%s",
        entry.entry_id,
        safe_door_ref(door.uid),
    )
    await door.callback()


async def _handle_add_face(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _get_entry(hass, str(call.data["entry_id"]))
    items = _normalize_faces(dict(call.data))
    _LOGGER.info("Action add_known_face: entry_id=%s count=%s", entry.entry_id, len(items))
    for item in items:
        image = await _load_image(hass, item)
        await entry.runtime_data.face_manager.async_add_known_face(
            str(item["name"]), image
        )
    await entry.runtime_data.background_processor.async_refresh_from_options()


async def _handle_remove_face(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _get_entry(hass, str(call.data["entry_id"]))
    name = str(call.data["name"]).strip()
    _LOGGER.info("Action remove_known_face: entry_id=%s", entry.entry_id)
    await entry.runtime_data.face_manager.async_remove_known_face(name)
    await entry.runtime_data.background_processor.async_refresh_from_options()


def _normalize_faces(data: dict[str, Any]) -> list[dict[str, Any]]:
    faces = data.get("faces")
    if faces:
        if any(data.get(key) for key in ("name", "image_url", "image_base64")):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="mixed_face_payload")
        if not isinstance(faces, list):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="faces_must_be_list")
        items = faces
    else:
        items = [
            {
                "name": data.get("name"),
                "image_url": data.get("image_url"),
                "image_base64": data.get("image_base64"),
            }
        ]

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="face_item_invalid")
        name = str(item.get("name") or "").strip()
        has_url = bool(item.get("image_url"))
        has_base64 = bool(item.get("image_base64"))
        if not name:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="face_name_required")
        if has_url == has_base64:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="face_source_required",
            )
        normalized.append({**item, "name": name})
    return normalized


async def _load_image(hass: HomeAssistant, item: dict[str, Any]) -> bytes:
    image_url = item.get("image_url")
    if image_url:
        parsed = urlsplit(str(image_url))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_image_url")
        session = async_get_clientsession(hass)
        try:
            async with asyncio.timeout(20):
                async with session.get(str(image_url)) as response:
                    if response.status != 200:
                        raise HomeAssistantError(
                            f"Не удалось загрузить изображение: HTTP {response.status}"
                        )
                    if response.content_length and response.content_length > SNAPSHOT_MAX_BYTES:
                        raise HomeAssistantError("Изображение слишком большое")
                    data = await response.read()
        except (ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError("Ошибка сети при загрузке изображения") from err
        if len(data) > SNAPSHOT_MAX_BYTES:
            raise HomeAssistantError("Изображение слишком большое")
        return data

    encoded = item.get("image_base64")
    try:
        data = base64.b64decode(str(encoded), validate=True)
    except (binascii.Error, ValueError) as err:
        raise HomeAssistantError("Некорректный image_base64") from err
    if len(data) > SNAPSHOT_MAX_BYTES:
        raise HomeAssistantError("Изображение слишком большое")
    return data
