"""Pure helpers for Intersvyaz HLS URLs and payload detection.

This module intentionally has no Home Assistant imports so URL/auth behaviour can
be regression-tested without loading HA. It never logs or persists credentials.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re
from urllib.parse import parse_qsl, quote_plus, urljoin, urlsplit, urlunsplit

_HLS_CONTENT_TYPES = ("mpegurl", "m3u8")
_SAFE_SUFFIX_RE = re.compile(r"\.[a-z0-9]{1,8}")


def build_upstream_headers(url: str, *, accept: str) -> dict[str, str]:
    """Build browser-like CDN headers and duplicate query bearer as Authorization.

    Intersvyaz media URLs carry ``token=bearer-...`` in the query. Some CDN edges
    accept the master playlist by query token but require/behave more reliably when
    the same credential is also present in Authorization for nested playlists and
    media resources. The value is never logged by callers.
    """

    headers = {
        "Accept": accept,
        "Origin": "https://cams.is74.ru",
        "Referer": "https://cams.is74.ru/",
    }
    token = query_token(url)
    if token:
        bearer = token[7:] if token.lower().startswith("bearer-") else token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
    return headers


def query_token(url: str) -> str | None:
    """Return the HLS query token without logging or normalising its value."""

    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key == "token" and value:
            return value
    return None


def inherit_hls_token(target_url: str, credential_url: str) -> str:
    """Copy ``token`` from credential_url when target_url lost it after redirect."""

    token = query_token(credential_url)
    if not token:
        return target_url

    parts = urlsplit(target_url)
    target_keys = {key for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if "token" in target_keys:
        return target_url

    separator = "&" if parts.query else ""
    query = f"{parts.query}{separator}token={quote_plus(token)}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def resolve_hls_reference(base_url: str, reference: str) -> str:
    """Resolve an HLS URI and explicitly inherit the current bearer query."""

    absolute = urljoin(base_url, reference)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"}:
        return absolute
    return inherit_hls_token(absolute, base_url)


def looks_like_playlist(data: bytes) -> bool:
    """Detect HLS by payload, tolerating UTF-8 BOM/whitespace and bad MIME types."""

    sample = data[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    return sample.startswith(b"#EXTM3U")


def is_playlist_hint(url: str, content_type: str, *, is_root: bool) -> bool:
    """Return True when path/MIME strongly hints that the response is a playlist."""

    if is_root:
        return True
    if ".m3u8" in urlsplit(url).path.lower():
        return True
    lowered = content_type.lower()
    return any(marker in lowered for marker in _HLS_CONTENT_TYPES)


def safe_suffix(url: str) -> str:
    """Keep a harmless short extension for local proxy resource IDs."""

    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if _SAFE_SUFFIX_RE.fullmatch(suffix or ""):
        return suffix
    return ".bin"
