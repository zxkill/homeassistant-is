"""Pure regression tests for HLS URL/auth helpers."""
import importlib.util
import sys
from pathlib import Path


def _load_utils():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz" / "yard_hls_utils.py"
    spec = importlib.util.spec_from_file_location("intersvyaz_yard_hls_utils_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


utils = _load_utils()


def test_relative_child_inherits_token():
    base = "https://cdn.example/master.m3u8?uuid=abc&token=bearer-secret"
    result = utils.resolve_hls_reference(base, "variant/720/index")
    assert result.startswith("https://cdn.example/variant/720/index?")
    assert "token=bearer-secret" in result


def test_redirected_base_recovers_token():
    original = "https://cdn.example/master.m3u8?uuid=abc&token=bearer-secret"
    redirected = "https://edge.example/session/master.m3u8?sid=1"
    result = utils.inherit_hls_token(redirected, original)
    assert "sid=1" in result
    assert "token=bearer-secret" in result


def test_existing_child_token_is_not_overwritten():
    base = "https://cdn.example/master.m3u8?token=bearer-parent"
    child = "https://edge.example/v.m3u8?token=bearer-child"
    assert utils.resolve_hls_reference(base, child) == child


def test_playlist_payload_detection_ignores_bad_mime_and_bom():
    assert utils.looks_like_playlist(b"\xef\xbb\xbf\r\n#EXTM3U\n#EXT-X-VERSION:3\n")
    assert not utils.looks_like_playlist(b"\x00\x00\x00\x18ftypisom")


def test_upstream_headers_duplicate_media_token_as_authorization():
    headers = utils.build_upstream_headers(
        "https://cdn.example/master.m3u8?token=bearer-deadbeef",
        accept="application/vnd.apple.mpegurl",
    )
    assert headers["Authorization"] == "Bearer deadbeef"
    assert headers["Origin"] == "https://cams.is74.ru"
    assert headers["Referer"] == "https://cams.is74.ru/"
    assert "deadbeef" not in repr({k: v for k, v in headers.items() if k != "Authorization"})
