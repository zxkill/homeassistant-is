"""Intersvyaz Doorphone integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IntersvyazApiClient, IntersvyazAuthError
from .background import DoorBackgroundProcessor
from .const import (
    CONF_AUTO_OPEN_COOLDOWN_SECONDS,
    CONF_BUYER_ID,
    CONF_CRM_TOKEN,
    CONF_DEVICE_ID,
    CONF_FACE_EVENT_COOLDOWN_SECONDS,
    CONF_KNOWN_FACES,
    CONF_MOBILE_TOKEN,
    CONF_RECOGNITION_MODE,
    CONF_RECOGNITION_REQUIRED_MATCHES,
    CONF_RECOGNITION_THRESHOLD,
    DEFAULT_BUYER_ID,
    DEFAULT_RECOGNITION_MODE,
    DOMAIN,
    FACE_EVENT_COOLDOWN_SECONDS,
    FACE_RECOGNITION_COOLDOWN_SECONDS,
    FACE_RECOGNITION_DISTANCE_THRESHOLD,
    FACE_REQUIRED_MATCHES_DEFAULT,
    RECOGNITION_MODE_AUTO_OPEN,
)
from .coordinator import IntersvyazDataUpdateCoordinator
from .door_manager import DoorManager
from .face_manager import FaceRecognitionManager
from .runtime import IntersvyazConfigEntry, IntersvyazRuntimeData
from .services import async_setup_services
from .snapshot import DoorSnapshotManager

_LOGGER = logging.getLogger("custom_components.intersvyaz")

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.EVENT,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide actions independently from config entries."""

    await async_setup_services(hass)
    _LOGGER.debug("Глобальные actions Intersvyaz готовы")
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
) -> bool:
    """Set up one Intersvyaz account."""

    _LOGGER.info(
        "Настройка Intersvyaz: entry_id=%s config_version=%s",
        entry.entry_id,
        entry.version,
    )
    data = dict(entry.data)
    api = IntersvyazApiClient(
        async_get_clientsession(hass),
        device_id=data.get(CONF_DEVICE_ID),
        buyer_id=int(data.get(CONF_BUYER_ID, DEFAULT_BUYER_ID)),
    )
    try:
        if data.get(CONF_MOBILE_TOKEN):
            api.set_mobile_token(data[CONF_MOBILE_TOKEN])
        if data.get(CONF_CRM_TOKEN):
            api.set_crm_token(data[CONF_CRM_TOKEN])
    except IntersvyazAuthError as err:
        raise ConfigEntryAuthFailed("Сохранённые credentials Интерсвязи некорректны") from err

    coordinator = IntersvyazDataUpdateCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    door_manager = DoorManager(hass, entry, api)
    try:
        await door_manager.async_setup()
    except IntersvyazAuthError as err:
        raise ConfigEntryAuthFailed("Требуется повторная авторизация Интерсвязи") from err

    snapshot_manager = DoorSnapshotManager(hass)
    face_manager = FaceRecognitionManager(hass, entry)
    runtime = IntersvyazRuntimeData(
        api=api,
        coordinator=coordinator,
        door_manager=door_manager,
        snapshot_manager=snapshot_manager,
        face_manager=face_manager,
    )
    entry.runtime_data = runtime

    background = DoorBackgroundProcessor(hass, entry)
    runtime.background_processor = background
    await background.async_setup()
    door_manager.start_periodic_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Intersvyaz готов: entry_id=%s doors=%s recognition=%s",
        entry.entry_id,
        len(runtime.doors),
        face_manager.recognition_mode,
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
) -> bool:
    """Unload one account and all its background resources."""

    _LOGGER.info("Выгрузка Intersvyaz: entry_id=%s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime = entry.runtime_data
    if runtime.background_processor is not None:
        runtime.background_processor.async_stop()
    runtime.door_manager.stop()
    runtime.snapshot_manager.invalidate()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: IntersvyazConfigEntry) -> bool:
    """Migrate 1.x/2.0-beta config entry options to stable 2.0 schema."""

    _LOGGER.info(
        "Миграция Intersvyaz entry_id=%s version=%s -> 3",
        entry.entry_id,
        entry.version,
    )
    if entry.version > 3:
        _LOGGER.error("ConfigEntry создан более новой версией интеграции")
        return False
    if entry.version == 3:
        return True

    options = dict(entry.options)
    had_faces = bool(options.get(CONF_KNOWN_FACES))
    # Старые версии открывали дверь после первого совпадения. Для уже существующей
    # конфигурации сохраняем поведение. Новые установки 2.0 по умолчанию безопаснее:
    # observe + два последовательных совпадения.
    options.setdefault(
        CONF_RECOGNITION_MODE,
        RECOGNITION_MODE_AUTO_OPEN if had_faces else DEFAULT_RECOGNITION_MODE,
    )
    options.setdefault(CONF_RECOGNITION_THRESHOLD, FACE_RECOGNITION_DISTANCE_THRESHOLD)
    options.setdefault(CONF_RECOGNITION_REQUIRED_MATCHES, 1 if had_faces else FACE_REQUIRED_MATCHES_DEFAULT)
    options.setdefault(CONF_AUTO_OPEN_COOLDOWN_SECONDS, FACE_RECOGNITION_COOLDOWN_SECONDS)
    options.setdefault(CONF_FACE_EVENT_COOLDOWN_SECONDS, FACE_EVENT_COOLDOWN_SECONDS)

    hass.config_entries.async_update_entry(entry, options=options, version=3)
    _LOGGER.info(
        "Миграция завершена: legacy_faces=%s mode=%s required=%s",
        had_faces,
        options[CONF_RECOGNITION_MODE],
        options[CONF_RECOGNITION_REQUIRED_MATCHES],
    )
    return True
