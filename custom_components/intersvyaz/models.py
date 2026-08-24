"""Внутренние модели runtime интеграции Intersvyaz."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

DoorOpenCallable = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class DoorRuntime:
    """Актуальные данные одного физического домофона."""

    uid: str
    mac: str
    door_id: int
    address: str
    is_main: bool
    is_shared: bool
    relay_id: int | str | None = None
    relay_num: int | None = None
    porch_num: str | int | None = None
    open_link: str | None = None
    image_url: str | None = None
    has_video: bool = False
    status_code: str | None = None
    status_text: str | None = None
    entrance_uid: str | None = None
    callback: DoorOpenCallable | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def update_from(self, other: "DoorRuntime") -> None:
        """Обновить изменяемые серверные поля, сохранив callback/identity."""

        self.mac = other.mac
        self.door_id = other.door_id
        self.address = other.address
        self.is_main = other.is_main
        self.is_shared = other.is_shared
        self.relay_id = other.relay_id
        self.relay_num = other.relay_num
        self.porch_num = other.porch_num
        self.open_link = other.open_link
        self.image_url = other.image_url
        self.has_video = other.has_video
        self.status_code = other.status_code
        self.status_text = other.status_text
        self.entrance_uid = other.entrance_uid
        self.metadata = dict(other.metadata)

    def public_summary(self) -> dict[str, Any]:
        """Безопасный технический summary без адреса/MAC/URL."""

        return {
            "uid": self.uid,
            "is_main": self.is_main,
            "is_shared": self.is_shared,
            "has_video": self.has_video,
            "status_code": self.status_code,
            "has_image_url": bool(self.image_url),
            "has_open_link": bool(self.open_link),
        }


@dataclass(slots=True)
class YardCameraRuntime:
    """Актуальные runtime-данные камеры из cams.is74.ru."""

    uid: str
    camera_id: int | None
    uuid: str
    group_id: str
    group_name: str
    name: str
    address: str
    porch: str | None
    live_access: bool
    archive_access: bool
    movement_access: bool
    snapshot_url: str | None
    snapshot_lossy_url: str | None
    hls_url: str | None
    low_latency_hls_url: str | None
    archive_hls_url: str | None
    mse_url: str | None
    realtime_ws_url: str | None
    latitude: float | None
    longitude: float | None
    matched_door_uid: str | None = None

    @property
    def has_hls_stream(self) -> bool:
        """Есть хотя бы один HLS live URL."""

        return bool(self.hls_url or self.low_latency_hls_url)

    @property
    def has_realtime_stream(self) -> bool:
        """Есть прямой MSE/Realtime WebSocket source от API."""

        return bool(self.mse_url or self.realtime_ws_url)

    @property
    def has_live_stream(self) -> bool:
        """Камера может участвовать в live-view."""

        return bool(self.has_realtime_stream or self.has_hls_stream)

    def update_from(self, other: "YardCameraRuntime") -> None:
        """Обновить временные media URL и метаданные, сохранив identity."""

        self.camera_id = other.camera_id
        self.group_id = other.group_id
        self.group_name = other.group_name
        self.name = other.name
        self.address = other.address
        self.porch = other.porch
        self.live_access = other.live_access
        self.archive_access = other.archive_access
        self.movement_access = other.movement_access
        self.snapshot_url = other.snapshot_url
        self.snapshot_lossy_url = other.snapshot_lossy_url
        self.hls_url = other.hls_url
        self.low_latency_hls_url = other.low_latency_hls_url
        self.archive_hls_url = other.archive_hls_url
        self.mse_url = other.mse_url
        self.realtime_ws_url = other.realtime_ws_url
        self.latitude = other.latitude
        self.longitude = other.longitude
        self.matched_door_uid = other.matched_door_uid
