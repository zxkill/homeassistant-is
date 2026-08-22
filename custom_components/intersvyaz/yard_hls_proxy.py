"""Локальный HLS proxy для камер Intersvyaz.

CDN `cams.is74.ru` отдаёт master playlist с bearer-параметром в query string,
но вложенные playlist/segment URL могут быть относительными. FFmpeg/go2rtc не
обязаны наследовать query master URL при переходе к относительному ресурсу,
поэтому прямой `MEDIA.HLS.*` URL способен открыться как playlist и затем оборваться.

Proxy оставляет bearer только внутри runtime Home Assistant, переписывает все HLS
URI на локальные URL и явно переносит upstream token на вложенные запросы.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote_plus, urljoin, urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import YardCameraRuntime
    from .runtime import IntersvyazConfigEntry
    from .yard_camera_manager import YardCameraManager

_LOGGER = logging.getLogger("custom_components.intersvyaz.yard_hls_proxy")

_PROXY_REGISTERED_KEY = f"{DOMAIN}_yard_hls_proxy_registered"
_PROXY_PATH = "/api/intersvyaz/hls/{entry_id}/{camera_ref}/{resource_id}"
_ROOT_RESOURCE = "master.m3u8"
_RESOURCE_TTL_SECONDS = 180
_MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
_URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')


@dataclass(slots=True)
class _ProxyResource:
    """Одно временное соответствие локального resource id upstream URL."""

    camera_ref: str
    upstream_url: str
    expires_at: float


async def async_setup_yard_hls_proxy(hass: HomeAssistant) -> None:
    """Зарегистрировать единственный HTTP view на весь Home Assistant."""

    if hass.data.get(_PROXY_REGISTERED_KEY):
        return
    hass.http.register_view(IntersvyazYardHlsProxyView)
    hass.data[_PROXY_REGISTERED_KEY] = True
    _LOGGER.info("[YARD_HLS_PROXY][VIEW_READY]")


class YardHlsProxy:
    """Runtime proxy одного config entry Intersvyaz."""

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

    def invalidate(self) -> None:
        """Удалить upstream media URL после обновления каталога камер."""

        self._roots.clear()
        self._camera_uids.clear()
        self._resources.clear()
        _LOGGER.debug("[YARD_HLS_PROXY][INVALIDATE] entry_id=%s", self._entry.entry_id)

    def build_stream_url(self, camera: YardCameraRuntime, upstream_url: str) -> str:
        """Сохранить upstream root и вернуть безопасный локальный stream URL."""

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
        """Проксировать master/media playlist или бинарный media resource."""

        if request.query.get("auth") != self._secret:
            return web.Response(status=HTTPStatus.UNAUTHORIZED, text="Invalid auth")

        self._prune_resources()
        if resource_id == _ROOT_RESOURCE:
            upstream_url = self._roots.get(camera_ref)
            if not upstream_url:
                return web.Response(status=HTTPStatus.NOT_FOUND, text="Unknown camera")
        else:
            resource = self._resources.get(resource_id)
            if resource is None or resource.camera_ref != camera_ref:
                return web.Response(status=HTTPStatus.NOT_FOUND, text="Unknown resource")
            resource.expires_at = time.monotonic() + _RESOURCE_TTL_SECONDS
            upstream_url = resource.upstream_url

        return await self._async_proxy_upstream(
            request,
            camera_ref=camera_ref,
            upstream_url=upstream_url,
            is_root=resource_id == _ROOT_RESOURCE,
        )

    async def _async_proxy_upstream(
        self,
        request: web.Request,
        *,
        camera_ref: str,
        upstream_url: str,
        is_root: bool,
    ) -> web.StreamResponse:
        headers = {
            "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,video/*,*/*",
            "Referer": "https://cams.is74.ru/",
        }
        if request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]

        try:
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
                if _is_playlist(final_url, content_type, is_root=is_root):
                    body = await response.content.read(_MAX_PLAYLIST_BYTES + 1)
                    if len(body) > _MAX_PLAYLIST_BYTES:
                        _LOGGER.warning(
                            "[YARD_HLS_PROXY][PLAYLIST_TOO_LARGE] camera=%s",
                            camera_ref,
                        )
                        return web.Response(status=HTTPStatus.BAD_GATEWAY)
                    if b"#EXTM3U" not in body[:4096]:
                        _LOGGER.warning(
                            "[YARD_HLS_PROXY][INVALID_PLAYLIST] camera=%s status=%s bytes=%s",
                            camera_ref,
                            response.status,
                            len(body),
                        )
                        return web.Response(status=HTTPStatus.BAD_GATEWAY)
                    text = body.decode("utf-8", errors="replace")
                    rewritten = self._rewrite_playlist(
                        camera_ref=camera_ref,
                        base_upstream_url=final_url,
                        playlist=text,
                    )
                    _LOGGER.debug(
                        "[YARD_HLS_PROXY][PLAYLIST] camera=%s root=%s bytes=%s resources=%s",
                        camera_ref,
                        is_root,
                        len(body),
                        len(self._resources),
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
                transferred = 0
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
        except ClientError as err:
            _LOGGER.warning(
                "[YARD_HLS_PROXY][NETWORK_ERROR] camera=%s error=%s",
                camera_ref,
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
        """Переписать URI playlist/segment/key на защищённые локальные URL."""

        output: list[str] = []
        for raw_line in playlist.splitlines():
            line = raw_line.strip()
            if not line:
                output.append(raw_line)
                continue
            if line.startswith("#"):
                output.append(
                    _URI_ATTRIBUTE_RE.sub(
                        lambda match: f'URI="{self._local_resource_url(camera_ref, base_upstream_url, match.group(1))}"',
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
        upstream_url = _resolve_hls_reference(base_upstream_url, reference)
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
        suffix = _safe_suffix(upstream_url)
        resource_id = f"{digest}{suffix}"
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
        if len(self._resources) > 4096:
            oldest = sorted(self._resources.items(), key=lambda item: item[1].expires_at)
            for key, _resource in oldest[: len(self._resources) - 3072]:
                self._resources.pop(key, None)


class IntersvyazYardHlsProxyView(HomeAssistantView):
    """HTTP endpoint, доступный только по runtime-secret из stream_source."""

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
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            proxy = entry.runtime_data.yard_camera_manager.hls_proxy
        except (AttributeError, RuntimeError):
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)
        return await proxy.async_handle(request, camera_ref, resource_id)


def _internal_base_url(hass: HomeAssistant) -> str:
    """Получить URL, доступный stream worker из самого Home Assistant."""

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


def _resolve_hls_reference(base_url: str, reference: str) -> str:
    """Разрешить URI и явно унаследовать bearer token master playlist."""

    absolute = urljoin(base_url, reference)
    base_parts = urlsplit(base_url)
    target_parts = urlsplit(absolute)
    if target_parts.scheme not in {"http", "https"}:
        return absolute

    base_query = dict(parse_qsl(base_parts.query, keep_blank_values=True))
    target_keys = {
        key for key, _value in parse_qsl(target_parts.query, keep_blank_values=True)
    }
    query = target_parts.query
    # CDN авторизует media resources через token query. RFC URL resolution не
    # переносит query master playlist на относительный child/segment URL.
    # Существующий query сохраняем байт-в-байт: там могут быть подписанные параметры.
    if "token" in base_query and "token" not in target_keys:
        separator = "&" if query else ""
        query = f"{query}{separator}token={quote_plus(base_query['token'])}"

    return urlunsplit(
        (
            target_parts.scheme,
            target_parts.netloc,
            target_parts.path,
            query,
            target_parts.fragment,
        )
    )


def _is_playlist(url: str, content_type: str, *, is_root: bool) -> bool:
    if is_root:
        return True
    if ".m3u8" in urlsplit(url).path.lower():
        return True
    lowered = content_type.lower()
    return "mpegurl" in lowered or "m3u8" in lowered


def _safe_suffix(url: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix or ""):
        return suffix
    return ".bin"


def _safe_camera_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
