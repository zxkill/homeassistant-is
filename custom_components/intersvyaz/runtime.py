"""Типизированное runtime-состояние config entry Intersvyaz."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.config_entries import ConfigEntry

from .api import IntersvyazApiClient
from .models import DoorRuntime, YardCameraRuntime

if TYPE_CHECKING:
    from .background import DoorBackgroundProcessor
    from .coordinator import IntersvyazDataUpdateCoordinator
    from .door_manager import DoorManager
    from .face_manager import FaceRecognitionManager
    from .yard_camera_manager import YardCameraManager
    from .snapshot import DoorSnapshotManager


@dataclass(slots=True)
class IntersvyazRuntimeData:
    """Всё неперсистентное состояние одного аккаунта."""

    api: IntersvyazApiClient
    coordinator: "IntersvyazDataUpdateCoordinator"
    door_manager: "DoorManager"
    snapshot_manager: "DoorSnapshotManager"
    face_manager: "FaceRecognitionManager"
    yard_camera_manager: "YardCameraManager"
    background_processor: "DoorBackgroundProcessor | None" = None
    last_visitors: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def doors(self) -> list[DoorRuntime]:
        return self.door_manager.doors

    @property
    def default_door(self) -> DoorRuntime | None:
        return self.door_manager.default_door

    @property
    def yard_cameras(self) -> list[YardCameraRuntime]:
        return self.yard_camera_manager.cameras

    @property
    def live_yard_cameras(self) -> list[YardCameraRuntime]:
        return self.yard_camera_manager.live_cameras


IntersvyazConfigEntry = ConfigEntry[IntersvyazRuntimeData]
