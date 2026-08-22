"""Камеры домофонов интеграции Intersvyaz."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CAMERA_FRAME_INTERVAL_SECONDS,
    DATA_DOOR_OPENERS,
    DATA_FACE_MANAGER,
    DATA_OPEN_DOOR,
    DOMAIN,
)
from .snapshot import get_snapshot_manager

_LOGGER = logging.getLogger(f"{DOMAIN}.camera")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Создать камеры для домофонов с поддержкой снимков."""

    entry_store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    door_entries = entry_store.get(DATA_DOOR_OPENERS, [])
    if not door_entries:
        _LOGGER.info(
            "Для entry_id=%s нет данных домофонов, камеры созданы не будут",
            entry.entry_id,
        )
        return

    cameras: list[IntersvyazDoorCamera] = []
    for door_entry in door_entries:
        if not door_entry.get("has_video"):
            _LOGGER.debug(
                "Пропускаем домофон uid=%s без видеоподдержки",
                door_entry.get("uid"),
            )
            continue
        if not door_entry.get("image_url"):
            _LOGGER.debug(
                "Пропускаем домофон uid=%s без ссылки на снимок",
                door_entry.get("uid"),
            )
            continue
        cameras.append(IntersvyazDoorCamera(hass, entry, door_entry))

    if not cameras:
        _LOGGER.info("Для entry_id=%s не найдено домофонов со снимками", entry.entry_id)
        return

    _LOGGER.info(
        "Добавляем %s камер домофона для entry_id=%s",
        len(cameras),
        entry.entry_id,
    )
    async_add_entities(cameras)


class IntersvyazDoorCamera(Camera):
    """Камера, отображающая актуальный снимок домофона."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        door_entry: Dict[str, Any],
    ) -> None:
        super().__init__()
        self._hass = hass
        self._entry = entry
        self._door_entry = door_entry
        self._door_uid = str(door_entry.get("uid") or f"{entry.entry_id}_door_camera")
        self._attr_has_entity_name = True
        address = door_entry.get("address") or "Домофон"
        self._attr_name = f"Камера домофона ({address})"
        self._attr_unique_id = f"{self._door_uid}_camera"
        self._attr_frame_interval = CAMERA_FRAME_INTERVAL_SECONDS
        self._attr_image_refresh_seconds = CAMERA_FRAME_INTERVAL_SECONDS
        self._attr_should_poll = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Домофон Интерсвязь",
        )

    def _current_entry(self) -> Dict[str, Any]:
        """Получить актуальные данные домофона из runtime-хранилища."""

        domain_store = self._hass.data.get(DOMAIN, {})
        entry_store = domain_store.get(self._entry.entry_id, {})
        for door in entry_store.get(DATA_DOOR_OPENERS, []):
            if door.get("uid") == self._door_uid:
                return door
        return self._door_entry

    async def async_added_to_hass(self) -> None:
        """Залогировать регистрацию камеры для диагностики."""

        entity_id = self.entity_id or "<entity_id не назначен>"
        _LOGGER.info(
            "Камера домофона uid=%s зарегистрирована: entity_id=%s refresh=%ss",
            self._door_uid,
            entity_id,
            CAMERA_FRAME_INTERVAL_SECONDS,
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Получить снимок через общий менеджер, исключая лишние HTTP-запросы."""

        door_entry = self._current_entry()
        image_url = door_entry.get("image_url")
        if not image_url:
            _LOGGER.warning("Для домофона uid=%s отсутствует ссылка на снимок", self._door_uid)
            return None

        manager = get_snapshot_manager(self._hass, self._entry)
        data = await manager.async_get_snapshot(self._door_uid, image_url)
        if not data:
            return None

        await self._async_try_face_recognition(data)
        return data

    async def _async_try_face_recognition(self, image: bytes) -> None:
        """Запустить локальное распознавание лиц для полученного кадра."""

        domain_store = self._hass.data.get(DOMAIN, {})
        entry_store = domain_store.get(self._entry.entry_id)
        if not entry_store:
            _LOGGER.debug(
                "Распознавание uid=%s пропущено: runtime entry отсутствует", self._door_uid
            )
            return
        manager = entry_store.get(DATA_FACE_MANAGER)
        if not manager:
            _LOGGER.debug(
                "Распознавание uid=%s пропущено: face manager не настроен", self._door_uid
            )
            return
        door_entry = self._current_entry()
        open_callback = door_entry.get("callback") or entry_store.get(DATA_OPEN_DOOR)
        await manager.async_process_image(self._door_uid, image, open_callback)
