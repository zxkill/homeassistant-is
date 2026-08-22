"""Единый загрузчик и кратковременный кеш снимков домофонов."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DATA_SNAPSHOT_MANAGER, DOMAIN, SNAPSHOT_CACHE_TTL_SECONDS

_LOGGER = logging.getLogger(f"{DOMAIN}.snapshot")


@dataclass
class _CachedSnapshot:
    data: bytes
    fetched_at: float
    image_url: str


class DoorSnapshotManager:
    """Загружает кадры без одновременных повторных HTTP-запросов."""

    def __init__(self, hass: HomeAssistant, *, cache_ttl: float = SNAPSHOT_CACHE_TTL_SECONDS) -> None:
        self._hass = hass
        self._cache_ttl = max(float(cache_ttl), 0.0)
        self._cache: dict[str, _CachedSnapshot] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def async_get_snapshot(self, door_uid: str, image_url: str) -> bytes | None:
        """Вернуть свежий снимок домофона либо кешированный кадр."""

        if not image_url:
            return None

        now = time.monotonic()
        cached = self._cache.get(door_uid)
        if self._is_cache_valid(cached, image_url, now):
            _LOGGER.debug("Используем кеш снимка домофона uid=%s", door_uid)
            return cached.data

        lock = self._locks.setdefault(door_uid, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._cache.get(door_uid)
            if self._is_cache_valid(cached, image_url, now):
                _LOGGER.debug("Используем кеш после ожидания загрузки uid=%s", door_uid)
                return cached.data

            session = async_get_clientsession(self._hass)
            started = time.monotonic()
            try:
                async with session.get(image_url) as response:
                    if response.status != 200:
                        _LOGGER.warning(
                            "Не удалось получить снимок домофона uid=%s: HTTP %s",
                            door_uid,
                            response.status,
                        )
                        return None
                    data = await response.read()
            except (ClientError, asyncio.TimeoutError) as err:
                _LOGGER.warning("Ошибка загрузки снимка uid=%s: %s", door_uid, err)
                return None

            elapsed_ms = (time.monotonic() - started) * 1000
            self._cache[door_uid] = _CachedSnapshot(
                data=data,
                fetched_at=time.monotonic(),
                image_url=image_url,
            )
            _LOGGER.debug(
                "Снимок uid=%s загружен: bytes=%s duration_ms=%.1f",
                door_uid,
                len(data),
                elapsed_ms,
            )
            return data

    def invalidate(self, door_uid: str | None = None) -> None:
        """Очистить кеш одного домофона или всех домофонов."""

        if door_uid is None:
            self._cache.clear()
            _LOGGER.debug("Кеш снимков домофонов полностью очищен")
            return
        self._cache.pop(door_uid, None)
        _LOGGER.debug("Кеш снимка домофона uid=%s очищен", door_uid)

    def _is_cache_valid(
        self,
        cached: _CachedSnapshot | None,
        image_url: str,
        now: float,
    ) -> bool:
        return bool(
            cached
            and cached.image_url == image_url
            and now - cached.fetched_at <= self._cache_ttl
        )


def get_snapshot_manager(hass: HomeAssistant, entry: ConfigEntry) -> DoorSnapshotManager:
    """Получить единый менеджер снимков для config entry."""

    domain_store = hass.data.setdefault(DOMAIN, {})
    entry_store = domain_store.setdefault(entry.entry_id, {})
    manager = entry_store.get(DATA_SNAPSHOT_MANAGER)
    if isinstance(manager, DoorSnapshotManager):
        return manager

    manager = DoorSnapshotManager(hass)
    entry_store[DATA_SNAPSHOT_MANAGER] = manager
    _LOGGER.info("Создан единый менеджер снимков для entry_id=%s", entry.entry_id)
    return manager
