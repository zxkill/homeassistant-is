"""Regression tests for the Intersvyaz rolling HLS adapter."""
import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz" / "yard_live_playlist.py"
    spec = importlib.util.spec_from_file_location("intersvyaz_yard_live_playlist_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live = _load_module()


def _clip(uri: str) -> str:
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:10.000000,\n"
        f"{uri}\n"
        "#EXT-X-ENDLIST\n"
    )


def test_single_segment_endlist_becomes_live_playlist():
    state = live.RollingPlaylistState()
    result = live.adapt_single_segment_live_playlist(_clip("/seg-a.ts"), state)

    assert result.adapted is True
    assert result.new_segment is True
    assert "#EXT-X-ENDLIST" not in result.playlist
    assert "#EXT-X-MEDIA-SEQUENCE:0" in result.playlist
    assert "/seg-a.ts" in result.playlist
    assert result.window_size == 1


def test_duplicate_poll_does_not_duplicate_segment():
    state = live.RollingPlaylistState()
    live.adapt_single_segment_live_playlist(_clip("/seg-a.ts"), state)
    result = live.adapt_single_segment_live_playlist(_clip("/seg-a.ts"), state)

    assert result.new_segment is False
    assert result.playlist.count("/seg-a.ts") == 1
    assert result.first_sequence == 0
    assert result.last_sequence == 0


def test_new_vendor_clip_advances_sequence_and_builds_window():
    state = live.RollingPlaylistState()
    live.adapt_single_segment_live_playlist(_clip("/seg-a.ts"), state)
    second = live.adapt_single_segment_live_playlist(_clip("/seg-b.ts"), state)

    assert second.new_segment is True
    assert second.first_sequence == 0
    assert second.last_sequence == 1
    assert second.window_size == 2
    assert second.playlist.index("/seg-a.ts") < second.playlist.index("/seg-b.ts")


def test_rolling_window_has_monotonic_media_sequence():
    state = live.RollingPlaylistState()
    for name in ("a", "b", "c", "d", "e"):
        result = live.adapt_single_segment_live_playlist(
            _clip(f"/seg-{name}.ts"), state, max_segments=3
        )

    assert result.first_sequence == 2
    assert result.last_sequence == 4
    assert "#EXT-X-MEDIA-SEQUENCE:2" in result.playlist
    assert "/seg-a.ts" not in result.playlist
    assert "/seg-b.ts" not in result.playlist
    assert "/seg-c.ts" in result.playlist
    assert "/seg-e.ts" in result.playlist


def test_normal_live_and_multi_segment_vod_are_not_modified():
    state = live.RollingPlaylistState()
    normal_live = _clip("/seg-a.ts").replace("#EXT-X-ENDLIST\n", "")
    assert live.adapt_single_segment_live_playlist(normal_live, state).adapted is False

    multi = _clip("/seg-a.ts").replace(
        "#EXT-X-ENDLIST\n",
        "#EXTINF:10.000000,\n/seg-b.ts\n#EXT-X-ENDLIST\n",
    )
    assert live.adapt_single_segment_live_playlist(multi, state).adapted is False
