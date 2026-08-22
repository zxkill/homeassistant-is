"""Проверка и выбор живого HLS-потока камер Intersvyaz.

Медиа-URL из cams.is74.ru содержат временный bearer в query string.
Этот модуль никогда не пишет URL в лог и проверяет только технические
метаданные ответа, чтобы токены не попадали в журнал Home Assistant.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

from aiohttp import ClientError, ClientSession

from .const import (
    YARD_STREAM_PROBE_CACHE_SECONDS,
    YARD_STREAM_PROBE_TIMEOUT_SECONDS,
)
from .models import YardCameraRuntime

_LOGGER = logging.getLogger("custom_components.intersvyaz.yard_stream")


class YardStreamResolver:
    """Выбирает рабочий HLS URL, не раскрывая временный bearer."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._cache: dict[str, tuple[str, float]] = {}

    def invalidate(self) -> None:
        """Сбросить результат probe после обновления каталога камер."""

        self._cache.clear()

    async def async_resolve(
        self, camera: YardCameraRuntime, *, force: bool = False
    ) -> str | None:
        """Вернуть первый доступный HLS источник.

        Используется только обычный MEDIA.HLS.LIVE.MAIN. Реальная диагностика
        CDN показала, что это стандартный rolling HLS; LOW_LATENCY/realtime=1
        намеренно не передаётся в стандартный PyAV/go2rtc pipeline Home Assistant.
        """

        now = time.monotonic()
        cached = self._cache.get(camera.uid)
        if not force and cached and cached[1] > now:
            return cached[0]

        # Реальный запрос к CDN показал, что MEDIA.HLS.LIVE.MAIN — обычный
        # rolling HLS (несколько TS-сегментов, растущий MEDIA-SEQUENCE, без
        # ENDLIST). LOW_LATENCY с realtime=1 через PyAV/go2rtc нестабилен,
        # поэтому сознательно не используем его как fallback стандартного
        # CameraEntityFeature.STREAM.
        candidates = (("main", camera.hls_url),)
        camera_ref = _safe_camera_ref(camera.uid)
        if not camera.hls_url and camera.low_latency_hls_url:
            _LOGGER.warning(
                "[YARD_STREAM][LOW_LATENCY_ONLY] camera=%s standard_main=false",
                camera_ref,
            )

        for mode, url in candidates:
            if not url:
                continue
            if await self._async_probe(url, camera_ref=camera_ref, mode=mode):
                self._cache[camera.uid] = (
                    url,
                    now + YARD_STREAM_PROBE_CACHE_SECONDS,
                )
                _LOGGER.info(
                    "[YARD_STREAM][SELECT] camera=%s mode=%s",
                    camera_ref,
                    mode,
                )
                return url

        self._cache.pop(camera.uid, None)
        _LOGGER.warning("[YARD_STREAM][NO_SOURCE] camera=%s", camera_ref)
        return None

    async def _async_probe(self, url: str, *, camera_ref: str, mode: str) -> bool:
        """Проверить, что URL действительно отдаёт HLS playlist."""

        try:
            async with asyncio.timeout(YARD_STREAM_PROBE_TIMEOUT_SECONDS):
                async with self._session.get(
                    url,
                    headers={
                        "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
                    },
                    allow_redirects=True,
                ) as response:
                    # Master playlist маленький. Ограничиваем чтение, чтобы probe
                    # никогда не начал случайно скачивать сам видеопоток.
                    body = await response.content.read(64 * 1024)
                    playlist = b"#EXTM3U" in body[:4096]
                    _LOGGER.debug(
                        "[YARD_STREAM][PROBE] camera=%s mode=%s status=%s "
                        "playlist=%s content_type=%s bytes=%s",
                        camera_ref,
                        mode,
                        response.status,
                        playlist,
                        response.headers.get("Content-Type", ""),
                        len(body),
                    )
                    return response.status == 200 and playlist
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "[YARD_STREAM][PROBE_FAILED] camera=%s mode=%s error=%s",
                camera_ref,
                mode,
                type(err).__name__,
            )
            return False


def _safe_camera_ref(value: str) -> str:
    """Короткая необратимая ссылка для логов вместо UUID/MAC/адреса."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
