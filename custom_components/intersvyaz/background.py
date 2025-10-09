"""Фоновая обработка снимков домофона для распознавания лиц."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CAMERA_FRAME_INTERVAL_SECONDS,
    CONF_BACKGROUND_CAMERAS,
    DATA_DOOR_OPENERS,
    DATA_FACE_MANAGER,
    DATA_OPEN_DOOR,
    DOMAIN,
)

_LOGGER = logging.getLogger(f"{DOMAIN}.background")


def _is_video_capable(door: dict[str, Any]) -> bool:
    """Проверить, доступна ли у домофона камера со снимком."""

    return bool(door.get("has_video")) and bool(door.get("image_url"))


def calculate_default_background_uids(
    entry: ConfigEntry, doors: Iterable[dict[str, Any]]
) -> list[str]:
    """Определить список домофонов для фоновой обработки по умолчанию."""

    candidates = [door for door in doors if _is_video_capable(door)]
    if not candidates:
        return []

    # В первую очередь выбираем основной подъезд, чтобы владельцу не приходилось
    # вручную включать самый востребованный домофон.
    main_candidates = [door for door in candidates if door.get("is_main")]
    if main_candidates:
        return [str(main_candidates[0].get("uid"))]

    # Если основного подъезда нет, используем первый доступный домофон с камерой.
    return [str(candidates[0].get("uid"))]


class DoorBackgroundProcessor:
    """Менеджер, который по таймеру забирает снимки и запускает распознавание."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        interval_seconds: float = CAMERA_FRAME_INTERVAL_SECONDS,
        scheduler: Callable[[HomeAssistant, Callable[[Optional[Any]], Any], timedelta], Callable[[], None]] = async_track_time_interval,
    ) -> None:
        # Экземпляр Home Assistant и запись конфигурации понадобятся для доступа
        # к общему хранилищу данных и опциям пользователя.
        self._hass = hass
        self._entry = entry
        # Интервал опроса камеры. Используем seconds, чтобы не зависеть от HA.
        self._interval = max(float(interval_seconds), 1.0)
        # Фабрика планировщика позволяет подменять расписание в тестах.
        self._scheduler = scheduler
        # Храним функцию отмены таймера, чтобы корректно останавливать обработку.
        self._unsubscribe: Callable[[], None] | None = None
        # Используем набор для быстрого контроля выбранных домофонов.
        self._selected_uids: set[str] = set()
        # Блокировка защищает выполнение цикла от конкурентных запусков.
        self._lock = asyncio.Lock()

    @property
    def selected_uids(self) -> set[str]:
        """Вернуть копию текущего списка домофонов для фоновой обработки."""

        return set(self._selected_uids)

    async def async_setup(self) -> None:
        """Инициализировать фоновую обработку на основании текущих опций."""

        await self.async_refresh_from_options(initial=True)

    def async_stop(self) -> None:
        """Остановить таймер фоновой обработки и очистить состояние."""

        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._selected_uids.clear()

    async def async_refresh_from_options(self, *, initial: bool = False) -> None:
        """Перечитать настройки пользователя и обновить расписание."""

        available = self._list_available_doors()
        if not available:
            _LOGGER.debug(
                "Для entry_id=%s нет домофонов с видео. Фоновая обработка отключена.",
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
                    "Для entry_id=%s применяем резервный список домофонов для фоновой"
                    " обработки: %s",
                    self._entry.entry_id,
                    ", ".join(sorted(desired)),
                )

        await self._async_apply_selection(desired, available)

    async def async_force_cycle(self) -> None:
        """Принудительно выполнить один цикл фоновой обработки (для тестов)."""

        await self._async_process_selected()

    async def _async_apply_selection(
        self, desired: set[str], available: dict[str, dict[str, Any]]
    ) -> None:
        """Запланировать обработку для выбранных домофонов и обновить таймер."""

        if desired == self._selected_uids:
            _LOGGER.debug(
                "Список фоновых домофонов для entry_id=%s не изменился: %s",
                self._entry.entry_id,
                ", ".join(sorted(desired)) or "<пусто>",
            )
            return

        self._selected_uids = {uid for uid in desired if uid in available}

        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

        if not self._selected_uids:
            _LOGGER.info(
                "Фоновая обработка домофонов отключена для entry_id=%s", self._entry.entry_id
            )
            return

        _LOGGER.info(
            "Для entry_id=%s фоново обрабатываются домофоны: %s",
            self._entry.entry_id,
            ", ".join(sorted(self._selected_uids)),
        )
        self._unsubscribe = self._scheduler(
            self._hass,
            lambda now: asyncio.create_task(self._async_process_selected()),
            timedelta(seconds=self._interval),
        )

    def _list_available_doors(self) -> dict[str, dict[str, Any]]:
        """Сформировать словарь домофонов, доступных для фоновой обработки."""

        domain_store = self._hass.data.get(DOMAIN, {})
        entry_store = domain_store.get(self._entry.entry_id, {})
        door_openers = entry_store.get(DATA_DOOR_OPENERS, []) or []
        result: dict[str, dict[str, Any]] = {}
        for door in door_openers:
            uid = door.get("uid")
            if not isinstance(uid, str):
                continue
            if not _is_video_capable(door):
                continue
            result[uid] = door
        return result

    async def _async_process_selected(self) -> None:
        """Загрузить снимки для всех выбранных домофонов и запустить распознавание."""

        if not self._selected_uids:
            return
        if self._lock.locked():
            _LOGGER.debug(
                "Пропускаем запуск фонового цикла для entry_id=%s: предыдущий ещё выполняется",
                self._entry.entry_id,
            )
            return

        async with self._lock:
            domain_store = self._hass.data.get(DOMAIN, {})
            entry_store = domain_store.get(self._entry.entry_id, {})
            manager = entry_store.get(DATA_FACE_MANAGER)
            if manager is None:
                _LOGGER.debug(
                    "Менеджер распознавания лиц недоступен, фоновая обработка entry_id=%s"
                    " пропущена",
                    self._entry.entry_id,
                )
                return

            default_open = entry_store.get(DATA_OPEN_DOOR)
            doors = self._list_available_doors()
            if not doors:
                _LOGGER.debug(
                    "Для entry_id=%s не осталось домофонов с видео, таймер будет очищен",
                    self._entry.entry_id,
                )
                self.async_stop()
                return

            session = async_get_clientsession(self._hass)
            for uid in list(self._selected_uids):
                door = doors.get(uid)
                if not door:
                    _LOGGER.info(
                        "Домофон uid=%s больше недоступен для entry_id=%s, удаляем из фонового"
                        " списка",
                        uid,
                        self._entry.entry_id,
                    )
                    self._selected_uids.discard(uid)
                    continue
                await self._async_process_single(session, manager, door, default_open)

            if not self._selected_uids:
                _LOGGER.info(
                    "Все домофоны были исключены из фоновой обработки entry_id=%s",
                    self._entry.entry_id,
                )
                if self._unsubscribe:
                    self._unsubscribe()
                    self._unsubscribe = None

    async def _async_process_single(
        self,
        session,
        manager,
        door: dict[str, Any],
        default_open: Callable[[], Any] | None,
    ) -> None:
        """Получить снимок конкретного домофона и передать его менеджеру лиц."""

        uid = str(door.get("uid"))
        image_url = door.get("image_url")
        if not image_url:
            _LOGGER.debug(
                "У домофона uid=%s отсутствует ссылка на снимок, фоновая обработка пропущена",
                uid,
            )
            return

        try:
            async with session.get(image_url) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "Не удалось получить снимок домофона uid=%s: HTTP %s", uid, response.status
                    )
                    return
                image_bytes = await response.read()
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "Ошибка фоновой загрузки снимка домофона uid=%s: %s", uid, err
            )
            return

        open_callback = door.get("callback") or default_open
        try:
            await manager.async_process_image(uid, image_bytes, open_callback)
        except Exception as err:  # pragma: no cover - защитная ветка для неожиданных ошибок
            _LOGGER.exception(
                "Не удалось обработать снимок домофона uid=%s во время фонового цикла: %s",
                uid,
                err,
            )

