"""Camera entities доступных пользователю камер Intersvyaz."""
from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAMERA_FRAME_INTERVAL_SECONDS
from .devices import door_device_info, yard_camera_device_info
from .models import DoorRuntime, YardCameraRuntime
from .runtime import IntersvyazConfigEntry

_LOGGER = logging.getLogger("custom_components.intersvyaz.camera")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Предпочесть полный каталог cams.is74.ru, сохранив relay fallback."""

    runtime = entry.runtime_data
    if runtime.live_yard_cameras:
        entities = [IntersvyazYardCamera(entry, camera) for camera in runtime.live_yard_cameras]
        _LOGGER.info(
            "[CAMERAS][SETUP] source=yard_api entry_id=%s count=%s",
            entry.entry_id,
            len(entities),
        )
    else:
        entities = [
            IntersvyazDoorCamera(entry, door)
            for door in runtime.doors
            if door.has_video and door.image_url
        ]
        _LOGGER.info(
            "[CAMERAS][SETUP] source=relay_fallback entry_id=%s count=%s",
            entry.entry_id,
            len(entities),
        )
    async_add_entities(entities)


class IntersvyazYardCamera(Camera):
    """Камера из `/api/yard-with-group` со snapshot и HLS live stream."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_camera"
    _attr_should_poll = False
    _attr_frame_interval = CAMERA_FRAME_INTERVAL_SECONDS
    _attr_image_refresh_seconds = CAMERA_FRAME_INTERVAL_SECONDS

    def __init__(self, entry: IntersvyazConfigEntry, camera: YardCameraRuntime) -> None:
        super().__init__()
        self._entry = entry
        self._camera_uid = camera.uid
        self._matched_door_uid = camera.matched_door_uid

        matched_door = (
            entry.runtime_data.door_manager.get(camera.matched_door_uid)
            if camera.matched_door_uid
            else None
        )
        # Для камеры физического домофона сохраняем старый unique_id, чтобы после
        # обновления не создавать дубль существующей camera entity.
        self._attr_unique_id = (
            f"{matched_door.uid}_camera"
            if matched_door is not None
            else f"yard_{camera.uuid.lower()}_camera"
        )
        self._attr_device_info = (
            door_device_info(entry.entry_id, matched_door)
            if matched_door is not None
            else yard_camera_device_info(entry.entry_id, camera)
        )
        if camera.hls_url or camera.low_latency_hls_url:
            self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def _camera(self) -> YardCameraRuntime | None:
        return self._entry.runtime_data.yard_camera_manager.get(self._camera_uid)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        camera = self._camera
        if camera is None or not camera.snapshot_url:
            return None

        runtime = self._entry.runtime_data
        image = await runtime.snapshot_manager.async_get_snapshot(
            camera.uid,
            camera.snapshot_url,
        )
        if not image:
            # MEDIA URL содержит временный bearer. Один раз обновляем каталог камер.
            if await runtime.yard_camera_manager.async_refresh():
                camera = self._camera
                if camera and camera.snapshot_url:
                    image = await runtime.snapshot_manager.async_get_snapshot(
                        camera.uid,
                        camera.snapshot_url,
                        force=True,
                    )
        if not image:
            return None

        recognition_uid, callback = self._recognition_target(camera)
        await runtime.face_manager.async_process_image(
            recognition_uid,
            image,
            callback,
        )
        return image

    async def stream_source(self) -> str | None:
        """Вернуть авторизованный HLS URL для штатного stream pipeline HA."""

        camera = self._camera
        if camera is None or not camera.live_access:
            return None
        # Обычный multivariant HLS выбран как наиболее совместимый с HA/ffmpeg.
        # LOW_LATENCY остаётся в runtime и может стать отдельной настройкой позже.
        return camera.hls_url or camera.low_latency_hls_url

    def _recognition_target(self, camera: YardCameraRuntime):
        runtime = self._entry.runtime_data
        if camera.matched_door_uid:
            door = runtime.door_manager.get(camera.matched_door_uid)
            if door is not None:
                return door.uid, door.callback
        return camera.uid, None


class IntersvyazDoorCamera(Camera):
    """Fallback camera на случай недоступности отдельного cameras API."""

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
