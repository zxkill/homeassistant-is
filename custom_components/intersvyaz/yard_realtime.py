"""Realtime media-source helpers for Intersvyaz yard cameras.

The API exposes a Flussonic MSE WebSocket source. Full go2rtc builds understand
the ``flussonic:`` source scheme directly. Home Assistant's managed go2rtc may
run with a restricted module set, therefore the caller must still verify that
its active WebRTC provider supports the returned source.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from .models import YardCameraRuntime


def build_realtime_source(camera: YardCameraRuntime) -> str | None:
    """Return a go2rtc Flussonic source without logging the signed URL."""

    raw_url = camera.mse_url or camera.realtime_ws_url
    return build_flussonic_source(raw_url)


def build_flussonic_source(raw_url: str | None) -> str | None:
    """Normalize ws/http variants from the API into a Flussonic source."""

    if not raw_url:
        return None

    value = raw_url.strip()
    if not value:
        return None
    if value.startswith("flussonic:"):
        return value

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss", "http", "https"}:
        return None

    if scheme == "http":
        scheme = "ws"
    elif scheme == "https":
        scheme = "wss"

    websocket_url = urlunsplit(
        (
            scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return f"flussonic:{websocket_url}"
