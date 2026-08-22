"""Adapter for Intersvyaz's short HLS "live" playlists.

The Intersvyaz CDN can expose a live camera as a media playlist that contains
exactly one ~10 second MPEG-TS segment and ``#EXT-X-ENDLIST``. This is a valid
finite HLS clip, but Home Assistant/PyAV/go2rtc correctly stop after that clip.

This module converts only that very specific vendor shape into a small rolling
live playlist. It is intentionally pure (no Home Assistant imports), so the
state machine can be regression-tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


_GLOBAL_PREFIXES = (
    "#EXT-X-VERSION:",
    "#EXT-X-TARGETDURATION:",
    "#EXT-X-INDEPENDENT-SEGMENTS",
    "#EXT-X-ALLOW-CACHE:",
)
_DYNAMIC_PREFIXES = (
    "#EXT-X-MEDIA-SEQUENCE:",
    "#EXT-X-DISCONTINUITY-SEQUENCE:",
    "#EXT-X-PLAYLIST-TYPE:",
)


@dataclass(slots=True)
class RollingSegment:
    """One already-rewritten local media segment."""

    sequence: int
    uri: str
    tags: tuple[str, ...]


@dataclass(slots=True)
class RollingPlaylistState:
    """Per-upstream-media-playlist rolling state."""

    next_sequence: int = 0
    segments: list[RollingSegment] = field(default_factory=list)
    announced: bool = False


@dataclass(slots=True, frozen=True)
class LiveAdaptResult:
    """Result of one playlist adaptation attempt."""

    playlist: str
    adapted: bool
    new_segment: bool = False
    first_sequence: int = 0
    last_sequence: int = 0
    window_size: int = 0


def adapt_single_segment_live_playlist(
    playlist: str,
    state: RollingPlaylistState,
    *,
    max_segments: int = 4,
) -> LiveAdaptResult:
    """Turn Intersvyaz's one-segment ENDLIST clip into a rolling live playlist.

    The adapter deliberately activates only when all of these conditions hold:
    - ``#EXT-X-ENDLIST`` is present;
    - exactly one non-comment media URI exists;
    - that URI has an ``#EXTINF`` tag before it.

    Normal VOD playlists and standards-compliant live playlists are returned
    unchanged.
    """

    lines = [line.rstrip("\r") for line in playlist.splitlines()]
    if "#EXT-X-ENDLIST" not in lines:
        return LiveAdaptResult(playlist=playlist, adapted=False)

    uri_indexes = [
        index
        for index, line in enumerate(lines)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(uri_indexes) != 1:
        return LiveAdaptResult(playlist=playlist, adapted=False)

    uri_index = uri_indexes[0]
    uri = lines[uri_index].strip()

    segment_start = uri_index - 1
    while segment_start >= 0:
        current = lines[segment_start].strip()
        if current.startswith("#EXTINF:"):
            break
        if current and not current.startswith("#"):
            break
        segment_start -= 1
    if segment_start < 0 or not lines[segment_start].strip().startswith("#EXTINF:"):
        return LiveAdaptResult(playlist=playlist, adapted=False)

    segment_tags = tuple(
        line
        for line in lines[segment_start:uri_index]
        if line.strip()
        and line.strip() != "#EXT-X-ENDLIST"
        and not line.strip().startswith(_DYNAMIC_PREFIXES)
    )

    new_segment = not state.segments or state.segments[-1].uri != uri
    if new_segment:
        state.segments.append(
            RollingSegment(
                sequence=state.next_sequence,
                uri=uri,
                tags=segment_tags,
            )
        )
        state.next_sequence += 1
        if len(state.segments) > max_segments:
            del state.segments[: len(state.segments) - max_segments]

    # Preserve only stable/global tags from the upstream playlist. Dynamic
    # sequence/ENDLIST/VOD tags are owned by this adapter.
    global_lines = ["#EXTM3U"]
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "#EXTM3U":
            continue
        if stripped.startswith(_GLOBAL_PREFIXES) and line not in global_lines:
            global_lines.append(line)

    first_sequence = state.segments[0].sequence
    last_sequence = state.segments[-1].sequence
    output = [*global_lines, f"#EXT-X-MEDIA-SEQUENCE:{first_sequence}"]
    for segment in state.segments:
        output.extend(segment.tags)
        output.append(segment.uri)

    # No ENDLIST: the HLS client will poll this media playlist again. Each poll
    # re-fetches the tiny upstream clip; when its segment URI changes, we append
    # it with our own monotonically increasing media sequence.
    return LiveAdaptResult(
        playlist="\n".join(output) + "\n",
        adapted=True,
        new_segment=new_segment,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        window_size=len(state.segments),
    )
