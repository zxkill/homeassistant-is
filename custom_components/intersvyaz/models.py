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
