"""Фоновая обработка снимков выбранных домофонов."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CAMERA_FRAME_INTERVAL_SECONDS,
    CONF_BACKGROUND_CAMERAS,
    RECOGNITION_MODE_OFF,
)
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.background")


class DoorBackgroundProcessor:
    """Периодически получает кадры и передаёт их локальному recognition engine."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        *,
        interval_seconds: float = CAMERA_FRAME_INTERVAL_SECONDS,
        scheduler: Callable = async_track_time_interval,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._interval = max(float(interval_seconds), 1.0)
        self._scheduler = scheduler
        self._unsubscribe: Callable[[], None] | None = None
        self._selected_uids: set[str] = set()
        self._lock = asyncio.Lock()
        self._pending = False
        self._snapshot_failures: dict[str, int] = {}

    @property
    def selected_uids(self) -> set[str]:
        return set(self._selected_uids)

    async def async_setup(self) -> None:
        await self.async_refresh_from_options(initial=True)

    def async_stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._selected_uids.clear()
        self._pending = False

    async def async_refresh_from_options(self, *, initial: bool = False) -> None:
        runtime = self._entry.runtime_data
        manager = runtime.face_manager
        if manager.recognition_mode == RECOGNITION_MODE_OFF:
            self.async_stop()
            _LOGGER.info("Фоновое распознавание выключено mode=off")
            return

        available = {
            door.uid: door
            for door in runtime.doors
            if door.has_video and bool(door.image_url)
        }
        if not available:
            self.async_stop()
            return

        option_value = self._entry.options.get(CONF_BACKGROUND_CAMERAS)
        if isinstance(option_value, list):
            desired = {uid for uid in option_value if uid in available}
        elif manager.list_known_face_names():
            # Миграционная совместимость 1.x: если лица уже были настроены,
            # основной видеодомофон продолжает работать без повторной настройки.
            main = next((door for door in available.values() if door.is_main), None)
            desired = {main.uid if main else next(iter(available))}
            if not initial:
                _LOGGER.info("Применена fallback background camera для старой конфигурации")
        else:
            desired = set()

        self._apply_selection(desired)

    def _apply_selection(self, desired: set[str]) -> None:
        if desired == self._selected_uids and self._unsubscribe:
            return
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._selected_uids = set(desired)
        if not self._selected_uids:
            _LOGGER.info("Фоновая обработка камер отключена")
            return
        self._unsubscribe = self._scheduler(
            self._hass,
            self._async_schedule_handler,
            timedelta(seconds=self._interval),
        )
        _LOGGER.info(
            "Фоновая обработка включена: entry_id=%s cameras=%s interval=%.1fs",
            self._entry.entry_id,
            len(self._selected_uids),
            self._interval,
        )

    async def _async_schedule_handler(self, _now: Any) -> None:
        await self._async_process_selected()

    async def async_force_cycle(self) -> None:
        await self._async_process_selected()

    async def _async_process_selected(self) -> None:
        if not self._selected_uids:
            return
        if self._lock.locked():
            self._pending = True
            return

        run_again = False
        async with self._lock:
            try:
                runtime = self._entry.runtime_data
                for uid in list(self._selected_uids):
                    door = runtime.door_manager.get(uid)
                    if door is None or not door.image_url:
                        self._selected_uids.discard(uid)
                        continue
                    image = await runtime.snapshot_manager.async_get_snapshot(
                        door.uid, door.image_url
                    )
                    if not image:
                        failures = self._snapshot_failures.get(uid, 0) + 1
                        self._snapshot_failures[uid] = failures
                        if failures >= 3:
                            _LOGGER.info(
                                "Три ошибки снимка door=%s; обновляем временные ссылки",
                                safe_door_ref(uid),
                            )
                            self._snapshot_failures[uid] = 0
                            await runtime.door_manager.async_refresh()
                        continue
                    self._snapshot_failures[uid] = 0
                    await runtime.face_manager.async_process_image(
                        uid,
                        image,
                        door.callback,
                    )
            finally:
                run_again = self._pending
                self._pending = False

        if run_again:
            self._hass.async_create_task(self._async_process_selected())
