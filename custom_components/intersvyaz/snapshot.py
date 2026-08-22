"""Единый загрузчик и кратковременный кеш снимков домофонов."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import SNAPSHOT_CACHE_TTL_SECONDS, SNAPSHOT_MAX_BYTES
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.snapshot")


@dataclass(slots=True)
class _CachedSnapshot:
    data: bytes
    fetched_at: float
    image_url: str


class DoorSnapshotManager:
    """Получает снимки, дедуплицирует параллельные запросы и кеширует кадр."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        cache_ttl: float = SNAPSHOT_CACHE_TTL_SECONDS,
        max_bytes: int = SNAPSHOT_MAX_BYTES,
    ) -> None:
        self._hass = hass
        self._cache_ttl = max(float(cache_ttl), 0.0)
        self._max_bytes = max(int(max_bytes), 1024)
        self._cache: dict[str, _CachedSnapshot] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def cache_size(self) -> int:
        """Количество кадров в кратковременном runtime-кеше."""

        return len(self._cache)

    async def async_get_snapshot(
        self,
        door_uid: str,
        image_url: str,
        *,
        force: bool = False,
    ) -> bytes | None:
        """Вернуть свежий либо кешированный снимок."""

        if not image_url:
            return None
        now = time.monotonic()
        cached = self._cache.get(door_uid)
        if not force and self._valid(cached, image_url, now):
            return cached.data

        lock = self._locks.setdefault(door_uid, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._cache.get(door_uid)
            if not force and self._valid(cached, image_url, now):
                return cached.data

            session = async_get_clientsession(self._hass)
            started = time.monotonic()
            try:
                async with session.get(image_url) as response:
                    if response.status != 200:
                        _LOGGER.warning(
                            "Снимок door=%s недоступен: HTTP %s",
                            safe_door_ref(door_uid),
                            response.status,
                        )
                        return None
                    content_length = response.content_length
                    if content_length and content_length > self._max_bytes:
                        _LOGGER.warning(
                            "Снимок door=%s отклонён: Content-Length=%s > limit=%s",
                            safe_door_ref(door_uid),
                            content_length,
                            self._max_bytes,
                        )
                        return None
                    data = await response.read()
            except (ClientError, asyncio.TimeoutError) as err:
                _LOGGER.warning(
                    "Ошибка загрузки снимка door=%s: %s",
                    safe_door_ref(door_uid),
                    type(err).__name__,
                )
                return None

            if not data or len(data) > self._max_bytes:
                _LOGGER.warning(
                    "Снимок door=%s имеет некорректный размер=%s",
                    safe_door_ref(door_uid),
                    len(data) if data else 0,
                )
                return None

            self._cache[door_uid] = _CachedSnapshot(
                data=data,
                fetched_at=time.monotonic(),
                image_url=image_url,
            )
            _LOGGER.debug(
                "Снимок door=%s загружен bytes=%s duration_ms=%.1f",
                safe_door_ref(door_uid),
                len(data),
                (time.monotonic() - started) * 1000,
            )
            return data

    def invalidate(self, door_uid: str | None = None) -> None:
        """Сбросить кеш одного/всех домофонов."""

        if door_uid is None:
            self._cache.clear()
            return
        self._cache.pop(door_uid, None)

    def _valid(
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
