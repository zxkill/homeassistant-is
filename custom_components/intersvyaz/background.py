"""Фоновая обработка снимков домофона для распознавания лиц."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CAMERA_FRAME_INTERVAL_SECONDS,
    CONF_BACKGROUND_CAMERAS,
    DATA_DOOR_OPENERS,
    DATA_FACE_MANAGER,
    DATA_OPEN_DOOR,
    DOMAIN,
)
from .snapshot import get_snapshot_manager

_LOGGER = logging.getLogger(f"{DOMAIN}.background")


def _is_video_capable(door: dict[str, Any]) -> bool:
    """Проверить, доступна ли у домофона камера со снимком."""

    return bool(door.get("has_video")) and bool(door.get("image_url"))


def calculate_default_background_uids(
    entry: ConfigEntry, doors: Iterable[dict[str, Any]]
) -> list[str]:
    """Определить домофон для фоновой обработки по умолчанию."""

    candidates = [door for door in doors if _is_video_capable(door)]
    if not candidates:
        return []
    main_candidates = [door for door in candidates if door.get("is_main")]
    if main_candidates:
        return [str(main_candidates[0].get("uid"))]
    return [str(candidates[0].get("uid"))]


class DoorBackgroundProcessor:
    """По таймеру получает снимки выбранных домофонов и анализирует их."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        interval_seconds: float = CAMERA_FRAME_INTERVAL_SECONDS,
        scheduler: Callable[
            [HomeAssistant, Callable[[Optional[Any]], Any], timedelta], Callable[[], None]
        ] = async_track_time_interval,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._interval = max(float(interval_seconds), 1.0)
        self._scheduler = scheduler
        self._unsubscribe: Callable[[], None] | None = None
        self._selected_uids: set[str] = set()
        self._lock = asyncio.Lock()
        self._has_pending_run = False

    @property
    def selected_uids(self) -> set[str]:
        """Вернуть копию списка домофонов для фоновой обработки."""

        return set(self._selected_uids)

    async def async_setup(self) -> None:
        """Инициализировать фоновую обработку из options."""

        await self.async_refresh_from_options(initial=True)

    def async_stop(self) -> None:
        """Остановить таймер фоновой обработки."""

        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._selected_uids.clear()
        self._has_pending_run = False

    async def async_refresh_from_options(self, *, initial: bool = False) -> None:
        """Перечитать настройки пользователя и обновить расписание."""

        available = self._list_available_doors()
        if not available:
            _LOGGER.debug(
                "Для entry_id=%s нет домофонов с видео; фоновая обработка отключена",
                self._entry.entry_id,
            )
            self.async_stop()
            return

        option_value = self._entry.options.get(CONF_BACKGROUND_CAMERAS)
        if isinstance(option_value, list):
            desired = {str(uid) for uid in option_value if str(uid) in available}
        else:
            desired = set(calculate_default_background_uids(self._entry, available.values()))
            if desired and not initial:
                _LOGGER.info(
                    "Для entry_id=%s применён резервный список фоновых камер: %s",
                    self._entry.entry_id,
                    ", ".join(sorted(desired)),
                )

        await self._async_apply_selection(desired, available)

    async def async_force_cycle(self) -> None:
        """Принудительно выполнить один цикл фоновой обработки."""

        await self._async_process_selected()

    async def _async_apply_selection(
        self, desired: set[str], available: dict[str, dict[str, Any]]
    ) -> None:
        if desired == self._selected_uids:
            _LOGGER.debug(
                "Список фоновых камер entry_id=%s не изменился: %s",
                self._entry.entry_id,
                ", ".join(sorted(desired)) or "<пусто>",
            )
            return

        self._selected_uids = {uid for uid in desired if uid in available}
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

        if not self._selected_uids:
            _LOGGER.info("Фоновая обработка отключена для entry_id=%s", self._entry.entry_id)
            return

        _LOGGER.info(
            "Фоновая обработка entry_id=%s: cameras=%s interval=%ss",
            self._entry.entry_id,
            ", ".join(sorted(self._selected_uids)),
            self._interval,
        )
        self._unsubscribe = self._scheduler(
            self._hass,
            self._async_schedule_handler,
            timedelta(seconds=self._interval),
        )

    def _list_available_doors(self) -> dict[str, dict[str, Any]]:
        domain_store = self._hass.data.get(DOMAIN, {})
        entry_store = domain_store.get(self._entry.entry_id, {})
        door_openers = entry_store.get(DATA_DOOR_OPENERS, []) or []
        result: dict[str, dict[str, Any]] = {}
        for door in door_openers:
            uid = door.get("uid")
            if isinstance(uid, str) and _is_video_capable(door):
                result[uid] = door
        return result

    async def _async_schedule_handler(self, now: Optional[Any]) -> None:
        _LOGGER.debug(
            "Сработал таймер фоновой обработки entry_id=%s time=%s",
            self._entry.entry_id,
            now,
        )
        await self._async_process_selected()

    async def _async_process_selected(self) -> None:
        if not self._selected_uids:
            return
        if self._lock.locked():
            _LOGGER.debug(
                "Фоновый цикл entry_id=%s уже выполняется; запрос отмечен как pending",
                self._entry.entry_id,
            )
            self._has_pending_run = True
            return

        run_again = False
        async with self._lock:
            try:
                entry_store = self._hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
                manager = entry_store.get(DATA_FACE_MANAGER)
                if manager is None:
                    _LOGGER.debug(
                        "Фоновый цикл entry_id=%s пропущен: face manager недоступен",
                        self._entry.entry_id,
                    )
                    return

                default_open = entry_store.get(DATA_OPEN_DOOR)
                doors = self._list_available_doors()
                if not doors:
                    self.async_stop()
                    return

                for uid in list(self._selected_uids):
                    door = doors.get(uid)
                    if not door:
                        self._selected_uids.discard(uid)
                        continue
                    await self._async_process_single(manager, door, default_open)

                if not self._selected_uids and self._unsubscribe:
                    self._unsubscribe()
                    self._unsubscribe = None
            finally:
                run_again = self._has_pending_run
                self._has_pending_run = False

        if run_again:
            self._hass.async_create_task(self._async_process_selected())

    async def _async_process_single(
        self,
        manager,
        door: dict[str, Any],
        default_open: Callable[[], Any] | None,
    ) -> None:
        uid = str(door.get("uid"))
        image_url = door.get("image_url")
        if not image_url:
            return

        snapshot_manager = get_snapshot_manager(self._hass, self._entry)
        image_bytes = await snapshot_manager.async_get_snapshot(uid, image_url)
        if not image_bytes:
            return

        open_callback = door.get("callback") or default_open
        try:
            await manager.async_process_image(uid, image_bytes, open_callback)
        except Exception as err:  # pragma: no cover - защитная ветка
            _LOGGER.exception(
                "Ошибка фонового анализа uid=%s entry_id=%s: %s",
                uid,
                self._entry.entry_id,
                err,
            )
