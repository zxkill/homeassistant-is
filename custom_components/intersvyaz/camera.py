"""Camera entities доступных пользователю камер Intersvyaz."""
from __future__ import annotations

from contextvars import ContextVar
import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAMERA_FRAME_INTERVAL_SECONDS
from .devices import door_device_info, yard_camera_device_info
from .models import DoorRuntime, YardCameraRuntime
from .runtime import IntersvyazConfigEntry

_LOGGER = logging.getLogger("custom_components.intersvyaz.camera")

# stream_source() используется и WebRTC provider, и HLS stream pipeline.
# ContextVar позволяет принудительно запросить HLS только в задаче создания
# HLS-потока, не мешая параллельному WebRTC/MSE просмотру.
_FORCE_HLS_SOURCE: ContextVar[bool] = ContextVar(
    "intersvyaz_force_hls_source",
    default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Предпочесть полный каталог cams.is74.ru, сохранив relay fallback."""

    runtime = entry.runtime_data
    if runtime.live_yard_cameras:
        entities = [
            IntersvyazYardCamera(entry, camera)
            for camera in runtime.live_yard_cameras
        ]
        _LOGGER.info(
            "[CAMERAS][SETUP] source=yard_api entry_id=%s count=%s realtime=%s",
            entry.entry_id,
            len(entities),
            sum(1 for camera in runtime.live_yard_cameras if camera.has_realtime_stream),
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
    """Камера двора со snapshot, HLS и опциональным realtime MSE/WebSocket."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_camera"
    _attr_should_poll = False
    _attr_frame_interval = CAMERA_FRAME_INTERVAL_SECONDS
    _attr_image_refresh_seconds = CAMERA_FRAME_INTERVAL_SECONDS

    # CDN Интерсвязи иногда отдаёт сегменты вида *.ts/<signed-token>.
    # FFmpeg/PyAV может считать последний компонент пути "расширением" и
    # отклонить его. Этот штатный stream option — дополнительная страховка
    # поверх нашего YardHlsCompatProxy.
    _attr_stream_options = {"allowed_extensions": "ALL"}

    def __init__(
        self,
        entry: IntersvyazConfigEntry,
        camera: YardCameraRuntime,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._camera_uid = camera.uid
        self._matched_door_uid = camera.matched_door_uid

        matched_door = (
            entry.runtime_data.door_manager.get(camera.matched_door_uid)
            if camera.matched_door_uid
            else None
        )

        # Для камеры физического домофона сохраняем старый unique_id, чтобы
        # после обновления не создавать дубль существующей camera entity.
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

        if camera.has_live_stream:
            self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def _camera(self) -> YardCameraRuntime | None:
        return self._entry.runtime_data.yard_camera_manager.get(
            self._camera_uid
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
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
            # MEDIA URL содержит временный bearer. Один раз обновляем каталог.
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
        """Return realtime MSE when provider supports it, otherwise safe HLS.

        Home Assistant first discovers a WebRTC provider using the HLS source.
        Once a provider is attached, a full go2rtc build may advertise support
        for ``flussonic:``. Only then do we expose MEDIA.MSE/REALTIME_WS to that
        provider. HA-managed go2rtc builds without the Flussonic module keep
        using HLS automatically.
        """

        camera = self._camera
        if camera is None or not camera.live_access:
            return None

        manager = self._entry.runtime_data.yard_camera_manager
        prefer_realtime = False
        realtime_source = manager.realtime_stream_source(camera.uid)

        if not _FORCE_HLS_SOURCE.get() and realtime_source:
            provider = self.webrtc_provider
            if provider is not None:
                try:
                    prefer_realtime = provider.async_is_supported(
                        realtime_source
                    )
                except Exception:  # pragma: no cover - provider isolation
                    _LOGGER.exception(
                        "[CAMERA][WEBRTC_PROVIDER_CHECK_FAILED] camera=%s",
                        self._attr_unique_id,
                    )

        _LOGGER.debug(
            "[CAMERA][STREAM_SOURCE] camera=%s provider=%s realtime_available=%s "
            "force_hls=%s selected=%s",
            self._attr_unique_id,
            self.webrtc_provider.domain if self.webrtc_provider else "none",
            bool(realtime_source),
            _FORCE_HLS_SOURCE.get(),
            "realtime_flussonic" if prefer_realtime else "hls",
        )

        return await manager.async_stream_source(
            camera.uid,
            prefer_realtime=prefer_realtime,
        )

    async def async_create_stream(self):
        """Force the fallback HLS worker to receive an FFmpeg-compatible URL.

        WebRTC may use a Flussonic WebSocket source, but the HA ``stream``
        integration still needs HLS. Context-local forcing keeps both paths
        correct even when they are created concurrently.
        """

        token = _FORCE_HLS_SOURCE.set(True)
        try:
            return await super().async_create_stream()
        finally:
            _FORCE_HLS_SOURCE.reset(token)

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

    def __init__(
        self,
        entry: IntersvyazConfigEntry,
        door: DoorRuntime,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._door_uid = door.uid
        self._attr_unique_id = f"{door.uid}_camera"
        self._attr_device_info = door_device_info(entry.entry_id, door)

    @property
    def _door(self) -> DoorRuntime | None:
        return self._entry.runtime_data.door_manager.get(self._door_uid)

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        door = self._door
        if door is None or not door.image_url:
            return None

        runtime = self._entry.runtime_data
        image = await runtime.snapshot_manager.async_get_snapshot(
            door.uid,
            door.image_url,
        )
        if not image:
            if await runtime.door_manager.async_refresh():
                door = self._door
                if door and door.image_url:
                    image = await runtime.snapshot_manager.async_get_snapshot(
                        door.uid,
                        door.image_url,
                        force=True,
                    )

        if not image:
            return None

        await runtime.face_manager.async_process_image(
            door.uid,
            image,
            door.callback,
        )
        return image
