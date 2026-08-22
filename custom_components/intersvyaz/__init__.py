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
    RECOGNITION_MODE_OBSERVE,
    RECOGNITION_MODES,
)
from .coordinator import IntersvyazDataUpdateCoordinator
from .door_manager import DoorManager
from .face_manager import FaceRecognitionManager
from .runtime import IntersvyazConfigEntry, IntersvyazRuntimeData
from .services import async_setup_services
from .snapshot import DoorSnapshotManager
from .yard_camera_manager import YardCameraManager

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

    yard_camera_manager = YardCameraManager(hass, entry, api, door_manager.doors)
    await yard_camera_manager.async_setup()

    snapshot_manager = DoorSnapshotManager(hass)
    face_manager = FaceRecognitionManager(hass, entry)
    runtime = IntersvyazRuntimeData(
        api=api,
        coordinator=coordinator,
        door_manager=door_manager,
        snapshot_manager=snapshot_manager,
        face_manager=face_manager,
        yard_camera_manager=yard_camera_manager,
    )
    entry.runtime_data = runtime

    background = DoorBackgroundProcessor(hass, entry)
    runtime.background_processor = background
    await background.async_setup()
    door_manager.start_periodic_refresh()
    yard_camera_manager.start_periodic_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Intersvyaz готов: entry_id=%s doors=%s yard_cameras=%s recognition=%s",
        entry.entry_id,
        len(runtime.doors),
        len(runtime.live_yard_cameras),
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
    await runtime.face_manager.async_stop()
    runtime.door_manager.stop()
    runtime.yard_camera_manager.stop()
    runtime.snapshot_manager.invalidate()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: IntersvyazConfigEntry) -> bool:
    """Migrate legacy config entries to the portable recognition schema."""

    target_version = 4
    _LOGGER.info(
        "[MIGRATION][BEGIN] entry_id=%s version=%s target=%s",
        entry.entry_id,
        entry.version,
        target_version,
    )
    if entry.version > target_version:
        _LOGGER.error(
            "[MIGRATION][ABORT] entry_id=%s newer_version=%s target=%s",
            entry.entry_id,
            entry.version,
            target_version,
        )
        return False
    if entry.version == target_version:
        return True

    options = dict(entry.options)
    stored_faces = options.get(CONF_KNOWN_FACES, [])
    legacy_face_count = len(stored_faces) if isinstance(stored_faces, list) else 0

    # Portable 2.0.7 uses a different descriptor scale than dlib/OpenCV. Never
    # carry an old auto-open decision policy into the new engine implicitly:
    # old descriptors remain stored for rollback/diagnostics but are ignored by
    # FaceRecognitionManager until the person is explicitly enrolled again.
    previous_mode = str(options.get(CONF_RECOGNITION_MODE, DEFAULT_RECOGNITION_MODE))
    if previous_mode not in RECOGNITION_MODES:
        previous_mode = DEFAULT_RECOGNITION_MODE
    if previous_mode == RECOGNITION_MODE_AUTO_OPEN:
        options[CONF_RECOGNITION_MODE] = RECOGNITION_MODE_OBSERVE
        _LOGGER.warning(
            "[MIGRATION][AUTO_OPEN_DISABLED] entry_id=%s reason=recognition_engine_changed",
            entry.entry_id,
        )
    else:
        options[CONF_RECOGNITION_MODE] = previous_mode

    options[CONF_RECOGNITION_THRESHOLD] = FACE_RECOGNITION_DISTANCE_THRESHOLD
    options[CONF_RECOGNITION_REQUIRED_MATCHES] = FACE_REQUIRED_MATCHES_DEFAULT
    options.setdefault(
        CONF_AUTO_OPEN_COOLDOWN_SECONDS, FACE_RECOGNITION_COOLDOWN_SECONDS
    )
    options.setdefault(CONF_FACE_EVENT_COOLDOWN_SECONDS, FACE_EVENT_COOLDOWN_SECONDS)

    hass.config_entries.async_update_entry(
        entry, options=options, version=target_version
    )
    _LOGGER.info(
        "[MIGRATION][DONE] entry_id=%s legacy_faces=%s mode=%s threshold=%.2f required=%s",
        entry.entry_id,
        legacy_face_count,
        options[CONF_RECOGNITION_MODE],
        options[CONF_RECOGNITION_THRESHOLD],
        options[CONF_RECOGNITION_REQUIRED_MATCHES],
    )
    return True

