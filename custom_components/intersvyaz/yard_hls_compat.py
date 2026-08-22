"""Minimal HLS compatibility proxy for Intersvyaz yard cameras.

The upstream HLS is already a correct rolling live stream. We do not rebuild,
buffer, or modify its timeline. The only incompatibility is FFmpeg's strict HLS
segment-extension check: Intersvyaz signs TS URLs as ``...segment.ts/<hash>``.
This proxy rewrites those segment references to local URLs ending in ``.ts``
and streams the original bytes unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

from aiohttp import ClientError, ClientSession, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN
from .yard_hls_rewrite import (
    first_playlist_reference,
    media_sequence,
    resolve_master_child,
    rewrite_media_playlist,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import YardCameraRuntime
    from .runtime import IntersvyazConfigEntry

_LOGGER = logging.getLogger("custom_components.intersvyaz.yard_hls_compat")

_PROXY_REGISTERED_KEY = f"{DOMAIN}_yard_hls_compat_registered"
_PROXY_PATH = "/api/intersvyaz/hls-compat/{entry_id}/{camera_ref}/{resource_id}"
_ROOT_RESOURCE = "live.m3u8"
_RESOURCE_TTL_SECONDS = 180
_PLAYLIST_LIMIT_BYTES = 256 * 1024
_UPSTREAM_TIMEOUT_SECONDS = 20


@dataclass(slots=True)
class _MediaResource:
    """Temporary local media resource mapped to an exact signed CDN URL."""

    camera_ref: str
    upstream_url: str
    expires_at: float


async def async_setup_yard_hls_compat(hass: HomeAssistant) -> None:
    """Register one compatibility endpoint shared by all config entries."""

    if hass.data.get(_PROXY_REGISTERED_KEY):
        return
    hass.http.register_view(IntersvyazYardHlsCompatView)
    hass.data[_PROXY_REGISTERED_KEY] = True
    _LOGGER.info("[YARD_HLS_COMPAT][VIEW_READY]")


class YardHlsCompatProxy:
    """Expose normal live HLS while normalising only media URL extensions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        session: ClientSession,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._session = session
        self._secret = secrets.token_urlsafe(32)
        self._masters: dict[str, str] = {}
        self._media_playlists: dict[str, str] = {}
        self._resources: dict[str, _MediaResource] = {}

    def invalidate(self) -> None:
        """Drop temporary URLs after catalogue/token refresh."""

        self._masters.clear()
        self._media_playlists.clear()
        self._resources.clear()
        _LOGGER.debug("[YARD_HLS_COMPAT][INVALIDATE] entry_id=%s", self._entry.entry_id)

    def build_stream_url(self, camera: YardCameraRuntime, master_url: str) -> str:
        """Return the local live playlist URL consumed by PyAV/go2rtc."""

        camera_ref = _safe_camera_ref(camera.uid)
        self._masters[camera_ref] = master_url
        base = _internal_base_url(self._hass)
        _LOGGER.info(
            "[YARD_HLS_COMPAT][SOURCE_READY] entry_id=%s camera=%s",
            self._entry.entry_id,
            camera_ref,
        )
        return (
            f"{base}/api/intersvyaz/hls-compat/{self._entry.entry_id}/"
            f"{camera_ref}/{_ROOT_RESOURCE}?auth={self._secret}"
        )

    async def async_handle(
        self,
        request: web.Request,
        camera_ref: str,
        resource_id: str,
    ) -> web.StreamResponse:
        """Serve one rewritten live playlist or one untouched media resource."""

        if request.query.get("auth") != self._secret:
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][LOCAL_DENY] camera=%s resource=%s",
                camera_ref,
                "playlist" if resource_id == _ROOT_RESOURCE else "media",
            )
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        self._prune_resources()
        if resource_id == _ROOT_RESOURCE:
            return await self._async_playlist(camera_ref)
        return await self._async_media(request, camera_ref, resource_id)

    async def _async_playlist(self, camera_ref: str) -> web.StreamResponse:
        master_url = self._masters.get(camera_ref)
        if not master_url:
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][PLAYLIST_MISS] camera=%s reason=no_master",
                camera_ref,
            )
            return web.Response(status=HTTPStatus.NOT_FOUND)

        media_url = self._media_playlists.get(camera_ref)
        if not media_url:
            media_url = await self._async_resolve_media_playlist(camera_ref, master_url)
            if not media_url:
                return web.Response(status=HTTPStatus.BAD_GATEWAY)
            self._media_playlists[camera_ref] = media_url

        fetched = await self._async_fetch_playlist(camera_ref, media_url, kind="media")
        if fetched is None:
            # The CDN token may have rotated. Re-resolve once from the current master.
            self._media_playlists.pop(camera_ref, None)
            media_url = await self._async_resolve_media_playlist(camera_ref, master_url)
            if not media_url:
                return web.Response(status=HTTPStatus.BAD_GATEWAY)
            self._media_playlists[camera_ref] = media_url
            fetched = await self._async_fetch_playlist(camera_ref, media_url, kind="media_retry")
            if fetched is None:
                return web.Response(status=HTTPStatus.BAD_GATEWAY)

        rewritten, segment_count = rewrite_media_playlist(
            fetched,
            base_url=media_url,
            local_url=lambda upstream_url, suffix: self._register_media(
                camera_ref,
                upstream_url,
                suffix,
            ),
        )
        _LOGGER.info(
            "[YARD_HLS_COMPAT][PLAYLIST_OK] camera=%s sequence=%s segments=%s",
            camera_ref,
            media_sequence(fetched) or "unknown",
            segment_count,
        )
        return web.Response(
            text=rewritten,
            content_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    async def _async_resolve_media_playlist(
        self,
        camera_ref: str,
        master_url: str,
    ) -> str | None:
        master = await self._async_fetch_playlist(camera_ref, master_url, kind="master")
        if master is None:
            return None

        reference = first_playlist_reference(master)
        if not reference:
            # Some providers return the media playlist directly. In that case the
            # master itself is already the correct upstream source.
            if "#EXTINF:" in master:
                _LOGGER.info(
                    "[YARD_HLS_COMPAT][MASTER_IS_MEDIA] camera=%s",
                    camera_ref,
                )
                return master_url
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][MASTER_INVALID] camera=%s reason=no_reference",
                camera_ref,
            )
            return None

        child_url = resolve_master_child(master_url, reference)
        _LOGGER.info("[YARD_HLS_COMPAT][MEDIA_RESOLVED] camera=%s", camera_ref)
        return child_url

    async def _async_fetch_playlist(
        self,
        camera_ref: str,
        url: str,
        *,
        kind: str,
    ) -> str | None:
        try:
            async with asyncio.timeout(_UPSTREAM_TIMEOUT_SECONDS):
                async with self._session.get(
                    url,
                    headers={
                        "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
                    },
                    allow_redirects=True,
                ) as response:
                    if response.status != HTTPStatus.OK:
                        _LOGGER.warning(
                            "[YARD_HLS_COMPAT][PLAYLIST_UPSTREAM_ERROR] "
                            "camera=%s kind=%s status=%s",
                            camera_ref,
                            kind,
                            response.status,
                        )
                        return None
                    body = await response.content.read(_PLAYLIST_LIMIT_BYTES + 1)
                    if len(body) > _PLAYLIST_LIMIT_BYTES:
                        _LOGGER.warning(
                            "[YARD_HLS_COMPAT][PLAYLIST_TOO_LARGE] camera=%s kind=%s",
                            camera_ref,
                            kind,
                        )
                        return None
                    text = body.decode("utf-8-sig", errors="replace")
                    if not text.lstrip().startswith("#EXTM3U"):
                        _LOGGER.warning(
                            "[YARD_HLS_COMPAT][PLAYLIST_INVALID] camera=%s kind=%s bytes=%s",
                            camera_ref,
                            kind,
                            len(body),
                        )
                        return None
                    _LOGGER.debug(
                        "[YARD_HLS_COMPAT][PLAYLIST_FETCH] camera=%s kind=%s bytes=%s",
                        camera_ref,
                        kind,
                        len(body),
                    )
                    return text
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][PLAYLIST_NETWORK_ERROR] camera=%s kind=%s error=%s",
                camera_ref,
                kind,
                type(err).__name__,
            )
            return None

    def _register_media(self, camera_ref: str, upstream_url: str, suffix: str) -> str:
        digest = hashlib.sha256(
            f"{self._secret}|{camera_ref}|{upstream_url}".encode("utf-8")
        ).hexdigest()[:24]
        # The suffix is intentionally the final path extension. This is the whole
        # compatibility fix for FFmpeg's HLS allowed_segment_extensions check.
        resource_id = f"{digest}{suffix}"
        self._resources[resource_id] = _MediaResource(
            camera_ref=camera_ref,
            upstream_url=upstream_url,
            expires_at=time.monotonic() + _RESOURCE_TTL_SECONDS,
        )
        base = _internal_base_url(self._hass)
        return (
            f"{base}/api/intersvyaz/hls-compat/{self._entry.entry_id}/"
            f"{camera_ref}/{resource_id}?auth={self._secret}"
        )

    async def _async_media(
        self,
        request: web.Request,
        camera_ref: str,
        resource_id: str,
    ) -> web.StreamResponse:
        resource = self._resources.get(resource_id)
        if resource is None or resource.camera_ref != camera_ref:
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][MEDIA_MISS] camera=%s",
                camera_ref,
            )
            return web.Response(status=HTTPStatus.NOT_FOUND)

        resource.expires_at = time.monotonic() + _RESOURCE_TTL_SECONDS
        headers = {"Accept": "*/*"}
        if range_header := request.headers.get("Range"):
            headers["Range"] = range_header

        try:
            async with asyncio.timeout(_UPSTREAM_TIMEOUT_SECONDS):
                async with self._session.get(
                    resource.upstream_url,
                    headers=headers,
                    allow_redirects=True,
                ) as upstream:
                    if upstream.status >= 400:
                        _LOGGER.warning(
                            "[YARD_HLS_COMPAT][MEDIA_UPSTREAM_ERROR] camera=%s status=%s",
                            camera_ref,
                            upstream.status,
                        )
                        return web.Response(status=HTTPStatus.BAD_GATEWAY)

                    response = web.StreamResponse(status=upstream.status)
                    if content_type := upstream.headers.get("Content-Type"):
                        response.content_type = content_type.split(";", 1)[0]
                    for header_name in ("Content-Range", "Accept-Ranges"):
                        if value := upstream.headers.get(header_name):
                            response.headers[header_name] = value
                    response.headers["Cache-Control"] = "no-store"
                    await response.prepare(request)

                    transferred = 0
                    async for chunk in upstream.content.iter_chunked(64 * 1024):
                        transferred += len(chunk)
                        await response.write(chunk)
                    await response.write_eof()
                    _LOGGER.debug(
                        "[YARD_HLS_COMPAT][MEDIA_OK] camera=%s bytes=%s",
                        camera_ref,
                        transferred,
                    )
                    return response
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][MEDIA_NETWORK_ERROR] camera=%s error=%s",
                camera_ref,
                type(err).__name__,
            )
            return web.Response(status=HTTPStatus.BAD_GATEWAY)

    def _prune_resources(self) -> None:
        now = time.monotonic()
        expired = [key for key, value in self._resources.items() if value.expires_at <= now]
        for key in expired:
            self._resources.pop(key, None)
        if expired:
            _LOGGER.debug(
                "[YARD_HLS_COMPAT][PRUNE] entry_id=%s removed=%s remaining=%s",
                self._entry.entry_id,
                len(expired),
                len(self._resources),
            )


class IntersvyazYardHlsCompatView(HomeAssistantView):
    """Runtime-secret protected HLS compatibility endpoint."""

    requires_auth = False
    url = _PROXY_PATH
    name = "api:intersvyaz:yard_hls_compat"

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        camera_ref: str,
        resource_id: str,
    ) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            _LOGGER.warning("[YARD_HLS_COMPAT][ENTRY_MISS]")
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            proxy = entry.runtime_data.yard_camera_manager.hls_compat_proxy
        except (AttributeError, RuntimeError):
            _LOGGER.warning(
                "[YARD_HLS_COMPAT][RUNTIME_UNAVAILABLE] entry_id=%s",
                entry_id,
            )
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)
        return await proxy.async_handle(request, camera_ref, resource_id)


def _internal_base_url(hass: HomeAssistant) -> str:
    """Return an URL reachable by HA stream worker and local go2rtc."""

    api = hass.config.api
    if api is not None and not api.use_ssl:
        return f"http://127.0.0.1:{api.port}"
    try:
        return get_url(
            hass,
            allow_internal=True,
            allow_external=True,
            allow_cloud=False,
            prefer_external=False,
        ).rstrip("/")
    except NoURLAvailableError:
        port = api.port if api is not None else 8123
        scheme = "https" if api is not None and api.use_ssl else "http"
        return f"{scheme}://127.0.0.1:{port}"


def _safe_camera_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
