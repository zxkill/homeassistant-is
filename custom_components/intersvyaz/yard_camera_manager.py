"""Обнаружение и обновление камер «Умного двора» Intersvyaz."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .api import IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError
from .const import YARD_CAMERA_REFRESH_INTERVAL_HOURS
from .models import DoorRuntime, YardCameraRuntime
from .runtime import IntersvyazConfigEntry
from .yard_models import YardCameraInfo, YardGroupInfo

_LOGGER = logging.getLogger("custom_components.intersvyaz.yard_camera_manager")


class YardCameraManager:
    """Поддерживает полный каталог доступных аккаунту камер двора."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        api: IntersvyazApiClient,
        doors: list[DoorRuntime],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._api = api
        self._doors = doors
        self._cameras: list[YardCameraRuntime] = []
        self._refresh_unsub = None
        self._reload_scheduled = False

    @property
    def cameras(self) -> list[YardCameraRuntime]:
        return self._cameras

    @property
    def live_cameras(self) -> list[YardCameraRuntime]:
        return [
            camera
            for camera in self._cameras
            if camera.live_access and (camera.snapshot_url or camera.hls_url)
        ]

    async def async_setup(self) -> None:
        """Первично получить каталог камер; ошибка камер не ломает домофон."""

        try:
            groups = await self._api.async_get_yard_groups()
        except (IntersvyazAuthError, IntersvyazApiError) as err:
            _LOGGER.warning(
                "[YARD_CAMERAS][SETUP_FAILED] entry_id=%s error=%s; "
                "используем relay-camera fallback",
                self._entry.entry_id,
                err,
            )
            groups = []

        self._cameras = self._build_cameras(groups)
        _LOGGER.info(
            "[YARD_CAMERAS][READY] entry_id=%s groups=%s cameras=%s live=%s matched_doors=%s",
            self._entry.entry_id,
            len(groups),
            len(self._cameras),
            len(self.live_cameras),
            sum(1 for item in self._cameras if item.matched_door_uid),
        )

    def start_periodic_refresh(self) -> None:
        if self._refresh_unsub:
            return
        self._refresh_unsub = async_track_time_interval(
            self._hass,
            self._async_scheduled_refresh,
            timedelta(hours=YARD_CAMERA_REFRESH_INTERVAL_HOURS),
        )
        _LOGGER.info(
            "[YARD_CAMERAS][REFRESH_SCHEDULED] entry_id=%s interval=%sh",
            self._entry.entry_id,
            YARD_CAMERA_REFRESH_INTERVAL_HOURS,
        )

    def stop(self) -> None:
        if self._refresh_unsub:
            self._refresh_unsub()
            self._refresh_unsub = None

    async def _async_scheduled_refresh(self, _now=None) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> bool:
        """Обновить media URL и при изменении состава перезагрузить entry."""

        try:
            groups = await self._api.async_get_yard_groups()
        except IntersvyazAuthError as err:
            _LOGGER.warning(
                "[YARD_CAMERAS][AUTH_FAILED] entry_id=%s error=%s; основной API продолжает работать",
                self._entry.entry_id,
                err,
            )
            return False
        except IntersvyazApiError as err:
            _LOGGER.warning(
                "[YARD_CAMERAS][REFRESH_FAILED] entry_id=%s error=%s",
                self._entry.entry_id,
                err,
            )
            return False

        fresh = self._build_cameras(groups)
        old_by_uid = {camera.uid: camera for camera in self._cameras}
        fresh_by_uid = {camera.uid: camera for camera in fresh}
        if set(old_by_uid) != set(fresh_by_uid):
            _LOGGER.info(
                "[YARD_CAMERAS][COMPOSITION_CHANGED] entry_id=%s old=%s new=%s; reload",
                self._entry.entry_id,
                len(old_by_uid),
                len(fresh_by_uid),
            )
            self._cameras = fresh
            if not self._reload_scheduled:
                self._reload_scheduled = True
                self._hass.async_create_task(
                    self._hass.config_entries.async_reload(self._entry.entry_id)
                )
            return True

        for uid, current in old_by_uid.items():
            current.update_from(fresh_by_uid[uid])
        try:
            self._entry.runtime_data.snapshot_manager.invalidate()
        except (AttributeError, RuntimeError):
            pass
        _LOGGER.debug(
            "[YARD_CAMERAS][REFRESH_OK] entry_id=%s cameras=%s",
            self._entry.entry_id,
            len(self._cameras),
        )
        return True

    def get(self, camera_uid: str) -> YardCameraRuntime | None:
        return next((camera for camera in self._cameras if camera.uid == camera_uid), None)

    def _build_cameras(self, groups: list[YardGroupInfo]) -> list[YardCameraRuntime]:
        result: list[YardCameraRuntime] = []
        seen: set[str] = set()
        claimed_doors: set[str] = set()
        for group in groups:
            for info in group.cameras:
                if info.uuid in seen:
                    continue
                seen.add(info.uuid)
                result.append(self._build_camera(group, info, claimed_doors))
        result.sort(key=lambda item: (item.group_name.lower(), _porch_sort(item.porch), item.name.lower()))
        return result

    def _build_camera(
        self,
        group: YardGroupInfo,
        info: YardCameraInfo,
        claimed_doors: set[str],
    ) -> YardCameraRuntime:
        matched = self._match_door(info)
        if matched is not None and matched.uid in claimed_doors:
            matched = None
        elif matched is not None:
            claimed_doors.add(matched.uid)
        return YardCameraRuntime(
            uid=f"{self._entry.entry_id}_yard_{info.uuid.lower()}",
            camera_id=info.camera_id,
            uuid=info.uuid,
            group_id=group.group_id,
            group_name=group.name,
            name=info.name,
            address=info.address,
            porch=info.porch,
            live_access=info.live_access,
            archive_access=info.archive_access,
            movement_access=info.movement_access,
            snapshot_url=info.snapshot_url,
            snapshot_lossy_url=info.snapshot_lossy_url,
            hls_url=info.hls_url,
            low_latency_hls_url=info.low_latency_hls_url,
            archive_hls_url=info.archive_hls_url,
            latitude=info.latitude,
            longitude=info.longitude,
            matched_door_uid=matched.uid if matched else None,
        )

    def _match_door(self, camera: YardCameraInfo) -> DoorRuntime | None:
        """Сопоставить camera API с relay API, не смешивая разные дома."""

        camera_address = _normalize_address(camera.address)
        exact = [door for door in self._doors if _normalize_address(door.address) == camera_address]
        if len(exact) == 1:
            return exact[0]

        porch = _normalize_porch(camera.porch)
        if not porch:
            return None
        base = _base_address(camera.address)
        candidates = [
            door
            for door in self._doors
            if _normalize_porch(door.porch_num) == porch
            and (not base or _base_address(door.address) == base)
        ]
        if len(candidates) == 1:
            return candidates[0]

        porch_only = [door for door in self._doors if _normalize_porch(door.porch_num) == porch]
        return porch_only[0] if len(porch_only) == 1 else None


def _normalize_address(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower().replace("ё", "е"))


def _base_address(value: str | None) -> str:
    text = _normalize_address(value)
    return re.sub(r",?\s*(?:п\.?|подъезд)\s*\d+\s*$", "", text).rstrip(" ,")


def _normalize_porch(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.lstrip("0") or "0"


def _porch_sort(value: str | None) -> tuple[int, str]:
    text = _normalize_porch(value) or ""
    try:
        return (0, f"{int(text):06d}")
    except ValueError:
        return (1, text)
