"""Regression tests for FFmpeg-compatible HLS URI rewriting."""
import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz" / "yard_hls_rewrite.py"
    spec = importlib.util.spec_from_file_location("intersvyaz_yard_hls_rewrite_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_infer_media_suffix_from_signed_intersvyaz_ts_path():
    url = (
        "/26/hls/media/live/main/camera_main_000594.ts/"
        "095648223e95955fa3472566eba7fe8a?i=1&ik=2"
    )
    assert mod.infer_media_suffix(url) == ".ts"


def test_rewrite_preserves_live_timeline_and_only_normalises_segment_urls():
    playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:16
#EXT-X-MEDIA-SEQUENCE:594
#EXTINF:16.266667,
/26/live/camera_000594.ts/signature-a?i=1&ik=2
#EXTINF:13.933333,
/26/live/camera_000595.ts/signature-b?i=1&ik=2
"""
    seen = []

    def local_url(upstream, suffix):
        seen.append((upstream, suffix))
        return f"http://127.0.0.1/compat/{len(seen)}{suffix}?auth=x"

    rewritten, count = mod.rewrite_media_playlist(
        playlist,
        base_url="https://cdn.example/hls/playlists/ts.m3u8?token=bearer-secret",
        local_url=local_url,
    )

    assert count == 2
    assert "#EXT-X-MEDIA-SEQUENCE:594" in rewritten
    assert "#EXT-X-TARGETDURATION:16" in rewritten
    assert "#EXTINF:16.266667," in rewritten
    assert "#EXT-X-ENDLIST" not in rewritten
    assert "/compat/1.ts?auth=x" in rewritten
    assert "/compat/2.ts?auth=x" in rewritten
    assert all(suffix == ".ts" for _, suffix in seen)


def test_resolve_master_child_inherits_bearer_only_when_missing():
    master = "https://cdn.example/master.m3u8?uuid=abc&token=bearer-secret"
    child = mod.resolve_master_child(master, "/hls/ts.m3u8?quality=main&uuid=abc")
    assert "token=bearer-secret" in child

    signed = mod.resolve_master_child(
        master,
        "/hls/ts.m3u8?quality=main&uuid=abc&token=child-token",
    )
    assert "token=child-token" in signed
    assert "bearer-secret" not in signed
