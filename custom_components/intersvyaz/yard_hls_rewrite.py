"""Pure helpers for FFmpeg-compatible Intersvyaz HLS playlists.

Intersvyaz media segments have paths such as ``...000594.ts/<signature>``.
Browsers accept them, but recent FFmpeg HLS demuxers validate the extension of
an HLS segment URL and reject this shape because the final path component is the
signature rather than ``.ts``.  These helpers preserve the playlist semantics
and only give proxied segment URLs a conventional media suffix.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qsl, quote_plus, urljoin, urlsplit, urlunsplit

_MEDIA_SUFFIX_RE = re.compile(
    r"\.(3gp|aac|ac3|avi|cmfa|cmfv|eac3|ec3|flac|fmp4|m2t|m2ts|m4a|m4s|m4v|"
    r"mkv|mov|mp2|mp3|mp4|mpeg|mpegts|mpg|mts|oga|ogg|ogv|ts|vob|vtt|wav|webvtt)"
    r"(?:/|$)",
    re.IGNORECASE,
)
_URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')


def infer_media_suffix(reference: str) -> str:
    """Return the FFmpeg-recognisable suffix hidden in an Intersvyaz URL path."""

    path = urlsplit(reference).path
    matches = list(_MEDIA_SUFFIX_RE.finditer(path))
    if not matches:
        return ".bin"
    return f".{matches[-1].group(1).lower()}"


def resolve_master_child(master_url: str, reference: str) -> str:
    """Resolve a child playlist and inherit the master bearer when necessary."""

    absolute = urljoin(master_url, reference)
    master_token = _query_value(master_url, "token")
    if not master_token or _query_value(absolute, "token"):
        return absolute

    parts = urlsplit(absolute)
    separator = "&" if parts.query else ""
    query = f"{parts.query}{separator}token={quote_plus(master_token)}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def first_playlist_reference(playlist: str) -> str | None:
    """Return the first non-comment URI from an HLS playlist."""

    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def media_sequence(playlist: str) -> str | None:
    """Return MEDIA-SEQUENCE for safe diagnostic logging."""

    prefix = "#EXT-X-MEDIA-SEQUENCE:"
    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip() or None
    return None


def rewrite_media_playlist(
    playlist: str,
    *,
    base_url: str,
    local_url: Callable[[str, str], str],
) -> tuple[str, int]:
    """Rewrite media/key URIs without changing HLS timing or live semantics.

    ``local_url`` receives the fully resolved upstream URL and the suffix that
    should appear at the end of the local path. No HLS tags are added, removed,
    reordered, or otherwise adapted.
    """

    output: list[str] = []
    media_count = 0

    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if not line:
            output.append(raw_line)
            continue

        if line.startswith("#"):
            output.append(
                _URI_ATTRIBUTE_RE.sub(
                    lambda match: _rewrite_uri_attribute(
                        match,
                        base_url=base_url,
                        local_url=local_url,
                    ),
                    raw_line,
                )
            )
            continue

        upstream_url = urljoin(base_url, line)
        suffix = infer_media_suffix(line)
        output.append(local_url(upstream_url, suffix))
        media_count += 1

    return "\n".join(output) + "\n", media_count


def _rewrite_uri_attribute(
    match: re.Match[str],
    *,
    base_url: str,
    local_url: Callable[[str, str], str],
) -> str:
    reference = match.group(1)
    if reference.startswith(("data:", "skd:")):
        return match.group(0)
    upstream_url = urljoin(base_url, reference)
    suffix = infer_media_suffix(reference)
    return f'URI="{local_url(upstream_url, suffix)}"'


def _query_value(url: str, name: str) -> str | None:
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key == name and value:
            return value
    return None
