"""Local HLS proxy for Intersvyaz yard cameras.

The camera CDN returns temporary bearer URLs. Home Assistant/PyAV/go2rtc must not
receive those credentials directly, and HLS children may lose the master query on
relative links or redirects. This proxy keeps credentials in runtime memory,
rewrites every HLS URI to a local URL, and proxies playlists/media itself.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN
from .yard_live_playlist import RollingPlaylistState, adapt_single_segment_live_playlist
from .yard_hls_utils import (
    build_upstream_headers,
    inherit_hls_token,
    is_playlist_hint,
    looks_like_playlist,
    resolve_hls_reference,
    safe_suffix,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import YardCameraRuntime
    from .runtime import IntersvyazConfigEntry
    from .yard_camera_manager import YardCameraManager

_LOGGER = logging.getLogger("custom_components.intersvyaz.yard_hls_proxy")

_PROXY_REGISTERED_KEY = f"{DOMAIN}_yard_hls_proxy_registered"
_PROXY_PATH = "/api/intersvyaz/hls/{entry_id}/{camera_ref}/{resource_id}"
_ROOT_RESOURCE = "master.m3u8"
_RESOURCE_TTL_SECONDS = 300
_MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
_MEDIA_PEEK_BYTES = 16 * 1024
_UPSTREAM_TIMEOUT_SECONDS = 20
_URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')


@dataclass(slots=True)
class _ProxyResource:
    """Temporary local resource ID -> upstream URL mapping."""

    camera_ref: str
    upstream_url: str
    expires_at: float


async def async_setup_yard_hls_proxy(hass: HomeAssistant) -> None:
    """Register a single HTTP view for all Intersvyaz config entries."""

    if hass.data.get(_PROXY_REGISTERED_KEY):
        return
    hass.http.register_view(IntersvyazYardHlsProxyView)
    hass.data[_PROXY_REGISTERED_KEY] = True
    _LOGGER.info("[YARD_HLS_PROXY][VIEW_READY]")


class YardHlsProxy:
    """Runtime HLS proxy owned by one Intersvyaz config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        session: ClientSession,
        manager: YardCameraManager,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._session = session
        self._manager = manager
        self._secret = secrets.token_urlsafe(32)
        self._roots: dict[str, str] = {}
        self._camera_uids: dict[str, str] = {}
        self._resources: dict[str, _ProxyResource] = {}
        self._rolling_playlists: dict[str, RollingPlaylistState] = {}

    def invalidate(self) -> None:
        """Forget temporary upstream URLs after camera catalogue refresh."""

        self._roots.clear()
        self._camera_uids.clear()
        self._resources.clear()
        self._rolling_playlists.clear()
        _LOGGER.debug("[YARD_HLS_PROXY][INVALIDATE] entry_id=%s", self._entry.entry_id)

    def build_stream_url(self, camera: YardCameraRuntime, upstream_url: str) -> str:
        """Store upstream root and return a local credential-free stream URL."""

        camera_ref = _safe_camera_ref(camera.uid)
        self._roots[camera_ref] = upstream_url
        self._camera_uids[camera_ref] = camera.uid
        base = _internal_base_url(self._hass)
        url = (
            f"{base}/api/intersvyaz/hls/{self._entry.entry_id}/"
            f"{camera_ref}/{_ROOT_RESOURCE}?auth={self._secret}"
        )
        _LOGGER.info(
            "[YARD_HLS_PROXY][SOURCE_READY] entry_id=%s camera=%s",
            self._entry.entry_id,
            camera_ref,
        )
        return url

    async def async_handle(
        self,
        request: web.Request,
        camera_ref: str,
        resource_id: str,
    ) -> web.StreamResponse:
        """Proxy one master/media playlist or binary media resource."""

        is_root = resource_id == _ROOT_RESOURCE
        if request.query.get("auth") != self._secret:
            _LOGGER.warning(
                "[YARD_HLS_PROXY][LOCAL_DENY] camera=%s root=%s reason=auth",
                camera_ref,
                is_root,
            )
            return web.Response(status=HTTPStatus.UNAUTHORIZED, text="Invalid auth")

        self._prune_resources()
        if is_root:
            upstream_url = self._roots.get(camera_ref)
            if not upstream_url:
                _LOGGER.warning(
                    "[YARD_HLS_PROXY][LOCAL_MISS] camera=%s root=true reason=no_root",
                    camera_ref,
                )
                return web.Response(status=HTTPStatus.NOT_FOUND, text="Unknown camera")
        else:
            resource = self._resources.get(resource_id)
            if resource is None or resource.camera_ref != camera_ref:
                _LOGGER.warning(
                    "[YARD_HLS_PROXY][LOCAL_MISS] camera=%s root=false reason=no_resource",
                    camera_ref,
                )
                return web.Response(status=HTTPStatus.NOT_FOUND, text="Unknown resource")
            resource.expires_at = time.monotonic() + _RESOURCE_TTL_SECONDS
            upstream_url = resource.upstream_url

        if is_root:
            _LOGGER.info("[YARD_HLS_PROXY][ROOT_REQUEST] camera=%s", camera_ref)

        return await self._async_proxy_upstream(
            request,
            camera_ref=camera_ref,
            upstream_url=upstream_url,
            is_root=is_root,
            resource_id=resource_id,
        )

    async def _async_proxy_upstream(
        self,
        request: web.Request,
        *,
        camera_ref: str,
        upstream_url: str,
        is_root: bool,
        resource_id: str,
    ) -> web.StreamResponse:
        headers = build_upstream_headers(
            upstream_url,
            accept="application/vnd.apple.mpegurl,application/x-mpegURL,video/*,*/*",
        )
        if request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]

        try:
            async with asyncio.timeout(_UPSTREAM_TIMEOUT_SECONDS):
                async with self._session.get(
                    upstream_url,
                    headers=headers,
                    allow_redirects=True,
                ) as response:
                    if response.status >= 400:
                        _LOGGER.warning(
                            "[YARD_HLS_PROXY][UPSTREAM_ERROR] camera=%s status=%s root=%s",
                            camera_ref,
                            response.status,
                            is_root,
                        )
                        return web.Response(status=HTTPStatus.BAD_GATEWAY)

                    content_type = response.headers.get("Content-Type", "")
                    final_url = str(response.url)
                    # Redirects may strip the credential query. Reattach it to the
                    # effective base before resolving children from this response.
                    effective_base_url = inherit_hls_token(final_url, upstream_url)

                    prefix = await response.content.read(_MEDIA_PEEK_BYTES)
                    playlist_by_payload = looks_like_playlist(prefix)
                    playlist_by_hint = is_playlist_hint(
                        final_url,
                        content_type,
                        is_root=is_root,
                    )
                    if playlist_by_payload or playlist_by_hint:
                        body = prefix + await response.content.read(
                            _MAX_PLAYLIST_BYTES + 1 - len(prefix)
                        )
                        if len(body) > _MAX_PLAYLIST_BYTES:
                            _LOGGER.warning(
                                "[YARD_HLS_PROXY][PLAYLIST_TOO_LARGE] camera=%s",
                                camera_ref,
                            )
                            return web.Response(status=HTTPStatus.BAD_GATEWAY)
                        if not looks_like_playlist(body):
                            _LOGGER.warning(
                                "[YARD_HLS_PROXY][INVALID_PLAYLIST] camera=%s "
                                "status=%s root=%s bytes=%s hinted=%s",
                                camera_ref,
                                response.status,
                                is_root,
                                len(body),
                                playlist_by_hint,
                            )
                            return web.Response(status=HTTPStatus.BAD_GATEWAY)

                        rewritten = self._rewrite_playlist(
                            camera_ref=camera_ref,
                            base_upstream_url=effective_base_url,
                            playlist=body.decode("utf-8-sig", errors="replace"),
                        )

                        # The Intersvyaz CDN currently exposes some "live" feeds
                        # as a finite one-segment (~10 s) HLS clip with ENDLIST.
                        # PyAV/go2rtc correctly stop at ENDLIST. Convert only this
                        # exact vendor shape into a rolling live media playlist.
                        if not is_root:
                            state = self._rolling_playlists.setdefault(
                                resource_id, RollingPlaylistState()
                            )
                            adapted = adapt_single_segment_live_playlist(
                                rewritten, state
                            )
                            if adapted.adapted:
                                rewritten = adapted.playlist
                                if adapted.new_segment or not state.announced:
                                    _LOGGER.info(
                                        "[YARD_HLS_PROXY][LIVE_ADAPT] camera=%s "
                                        "new_segment=%s sequence=%s..%s window=%s",
                                        camera_ref,
                                        adapted.new_segment,
                                        adapted.first_sequence,
                                        adapted.last_sequence,
                                        adapted.window_size,
                                    )
                                    state.announced = True
                                else:
                                    _LOGGER.debug(
                                        "[YARD_HLS_PROXY][LIVE_WAIT] camera=%s "
                                        "sequence=%s window=%s",
                                        camera_ref,
                                        adapted.last_sequence,
                                        adapted.window_size,
                                    )

                        _LOGGER.info(
                            "[YARD_HLS_PROXY][PLAYLIST_OK] camera=%s root=%s "
                            "bytes=%s resources=%s redirected=%s",
                            camera_ref,
                            is_root,
                            len(body),
                            len(self._resources),
                            final_url != upstream_url,
                        )
                        return web.Response(
                            text=rewritten,
                            content_type="application/vnd.apple.mpegurl",
                            headers={"Cache-Control": "no-store"},
                        )

                    proxy_response = web.StreamResponse(status=response.status)
                    if content_type:
                        proxy_response.content_type = content_type.split(";", 1)[0]
                    for header_name in ("Content-Range", "Accept-Ranges"):
                        if value := response.headers.get(header_name):
                            proxy_response.headers[header_name] = value
                    proxy_response.headers["Cache-Control"] = "no-store"
                    await proxy_response.prepare(request)
                    transferred = len(prefix)
                    if prefix:
                        await proxy_response.write(prefix)
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        transferred += len(chunk)
                        await proxy_response.write(chunk)
                    await proxy_response.write_eof()
                    _LOGGER.debug(
                        "[YARD_HLS_PROXY][MEDIA] camera=%s status=%s bytes=%s",
                        camera_ref,
                        response.status,
                        transferred,
                    )
                    return proxy_response
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "[YARD_HLS_PROXY][NETWORK_ERROR] camera=%s root=%s error=%s",
                camera_ref,
                is_root,
                type(err).__name__,
            )
            return web.Response(status=HTTPStatus.BAD_GATEWAY)

    def _rewrite_playlist(
        self,
        *,
        camera_ref: str,
        base_upstream_url: str,
        playlist: str,
    ) -> str:
        """Rewrite playlist/segment/key URIs to protected local URLs."""

        output: list[str] = []
        for raw_line in playlist.splitlines():
            line = raw_line.strip()
            if not line:
                output.append(raw_line)
                continue
            if line.startswith("#"):
                output.append(
                    _URI_ATTRIBUTE_RE.sub(
                        lambda match: (
                            f'URI="{self._local_resource_url(camera_ref, base_upstream_url, match.group(1))}"'
                        ),
                        raw_line,
                    )
                )
                continue
            output.append(self._local_resource_url(camera_ref, base_upstream_url, line))
        return "\n".join(output) + "\n"

    def _local_resource_url(
        self,
        camera_ref: str,
        base_upstream_url: str,
        reference: str,
    ) -> str:
        if reference.startswith(("data:", "skd:")):
            return reference
        upstream_url = resolve_hls_reference(base_upstream_url, reference)
        resource_id = self._register_resource(camera_ref, upstream_url)
        base = _internal_base_url(self._hass)
        return (
            f"{base}/api/intersvyaz/hls/{self._entry.entry_id}/"
            f"{camera_ref}/{resource_id}?auth={self._secret}"
        )

    def _register_resource(self, camera_ref: str, upstream_url: str) -> str:
        digest = hashlib.sha256(
            f"{self._secret}|{camera_ref}|{upstream_url}".encode("utf-8")
        ).hexdigest()[:24]
        resource_id = f"{digest}{safe_suffix(upstream_url)}"
        self._resources[resource_id] = _ProxyResource(
            camera_ref=camera_ref,
            upstream_url=upstream_url,
            expires_at=time.monotonic() + _RESOURCE_TTL_SECONDS,
        )
        return resource_id

    def _prune_resources(self) -> None:
        now = time.monotonic()
        expired = [key for key, value in self._resources.items() if value.expires_at <= now]
        for key in expired:
            self._resources.pop(key, None)
            self._rolling_playlists.pop(key, None)
        if len(self._resources) > 4096:
            oldest = sorted(self._resources.items(), key=lambda item: item[1].expires_at)
            for key, _resource in oldest[: len(self._resources) - 3072]:
                self._resources.pop(key, None)


class IntersvyazYardHlsProxyView(HomeAssistantView):
    """HTTP endpoint protected by a runtime-only secret."""

    requires_auth = False
    url = _PROXY_PATH
    name = "api:intersvyaz:yard_hls"

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
            _LOGGER.warning("[YARD_HLS_PROXY][ENTRY_MISS]")
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            proxy = entry.runtime_data.yard_camera_manager.hls_proxy
        except (AttributeError, RuntimeError):
            _LOGGER.warning("[YARD_HLS_PROXY][RUNTIME_UNAVAILABLE] entry_id=%s", entry_id)
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
