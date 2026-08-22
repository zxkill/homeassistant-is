"""Camera entities домофонов Intersvyaz."""
from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAMERA_FRAME_INTERVAL_SECONDS
from .devices import door_device_info
from .models import DoorRuntime
from .runtime import IntersvyazConfigEntry

_LOGGER = logging.getLogger("custom_components.intersvyaz.camera")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    cameras = [
        IntersvyazDoorCamera(entry, door)
        for door in entry.runtime_data.doors
        if door.has_video and door.image_url
    ]
    _LOGGER.info("Создаём camera entities: entry_id=%s count=%s", entry.entry_id, len(cameras))
    async_add_entities(cameras)


class IntersvyazDoorCamera(Camera):
    """Стандартная камера с актуальным snapshot домофона."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_camera"
    _attr_should_poll = False
    _attr_frame_interval = CAMERA_FRAME_INTERVAL_SECONDS
    _attr_image_refresh_seconds = CAMERA_FRAME_INTERVAL_SECONDS

    def __init__(self, entry: IntersvyazConfigEntry, door: DoorRuntime) -> None:
        super().__init__()
        self._entry = entry
        self._door_uid = door.uid
        self._attr_unique_id = f"{door.uid}_camera"
        self._attr_device_info = door_device_info(entry.entry_id, door)

    @property
    def _door(self) -> DoorRuntime | None:
        return self._entry.runtime_data.door_manager.get(self._door_uid)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        door = self._door
        if door is None or not door.image_url:
            return None

        runtime = self._entry.runtime_data
        image = await runtime.snapshot_manager.async_get_snapshot(
            door.uid, door.image_url
        )
        if not image:
            # URL снимка временный. После ошибки один раз обновляем relays и повторяем.
            if await runtime.door_manager.async_refresh():
                door = self._door
                if door and door.image_url:
                    image = await runtime.snapshot_manager.async_get_snapshot(
                        door.uid, door.image_url, force=True
                    )
        if not image:
            return None

        await runtime.face_manager.async_process_image(
            door.uid,
            image,
            door.callback,
        )
        return image
