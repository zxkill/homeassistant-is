"""Regression tests for the full yard camera catalogue."""
import importlib.util
import sys
from pathlib import Path


def _load_yard_models():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz" / "yard_models.py"
    spec = importlib.util.spec_from_file_location("intersvyaz_yard_models_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


yard_models = _load_yard_models()
parse_yard_groups = yard_models.parse_yard_groups


def test_yard_response_parser_extracts_media_without_raw_payload():
    payload = [
        {
            "id": "6196",
            "groupName": "Test yard",
            "cameras": [
                {
                    "ID": 33724,
                    "UUID": "62f100ab-8e10-43ec-bbbc-e03bdf67e7c0",
                    "NAME": "Intercom entrance 4",
                    "ADDRESS": "Test street, 1, entrance 4",
                    "PORCH": "4",
                    "ACCESS": {
                        "LIVE": {"STATUS": True, "REASON": ""},
                        "ARCHIVE": {"STATUS": True, "REASON": ""},
                        "MOVEMENT": {"STATUS": True, "REASON": ""},
                    },
                    "MEDIA": {
                        "SNAPSHOT": {
                            "LIVE": {
                                "MAIN": "https://cdn.example/snapshot?token=secret",
                                "LOSSY": "https://cdn.example/snapshot?lossy=1&token=secret",
                            }
                        },
                        "HLS": {
                            "LIVE": {
                                "MAIN": "https://cdn.example/live.m3u8?token=secret",
                                "LOW_LATENCY": "https://cdn.example/live.m3u8?rt=1&token=secret",
                            },
                            "ARCHIVE": "https://cdn.example/archive.m3u8?token=secret",
                        },
                    },
                    "POSITION": {"LATITUDE": 53.3, "LONGITUDE": 58.9},
                }
            ],
        }
    ]

    groups = parse_yard_groups(payload)
    assert len(groups) == 1
    camera = groups[0].cameras[0]
    assert camera.uuid == "62f100ab-8e10-43ec-bbbc-e03bdf67e7c0"
    assert camera.porch == "4"
    assert camera.live_access is True
    assert camera.archive_access is True
    assert camera.snapshot_url.endswith("token=secret")
    assert camera.hls_url.endswith("token=secret")
    assert camera.low_latency_hls_url is not None
    assert camera.latitude == 53.3
    assert camera.longitude == 58.9
    assert not hasattr(camera, "raw")


def test_camera_platform_prefers_yard_api_and_keeps_relay_fallback(component_root):
    source = (component_root / "camera.py").read_text()
    assert "runtime.live_yard_cameras" in source
    assert "IntersvyazYardCamera" in source
    assert "stream_source" in source
    assert "CameraEntityFeature.STREAM" in source
    assert "yard_camera_manager.async_stream_source" in source
    assert "relay_fallback" in source
    assert 'f"{matched_door.uid}_camera"' in source


def test_yard_api_uses_separate_endpoint_and_safe_runtime(component_root):
    api = (component_root / "api.py").read_text()
    const = (component_root / "const.py").read_text()
    diagnostics = (component_root / "diagnostics.py").read_text()
    assert "async_get_yard_groups" in api
    assert 'YARD_WITH_GROUP_ENDPOINT = "/api/yard-with-group"' in const
    assert 'DEFAULT_CAMERAS_BASE_URL = "https://cams.is74.ru"' in const
    assert '"snapshot_url":' not in diagnostics
    assert '"hls_url":' not in diagnostics
    assert '"has_snapshot_url"' in diagnostics
    assert '"has_hls_url"' in diagnostics


def test_background_can_use_yard_camera_and_never_auto_open_camera_only(component_root):
    source = (component_root / "background.py").read_text()
    assert "runtime.live_yard_cameras" in source
    assert "camera.matched_door_uid" in source
    assert "camera.uid," in source
    assert "None," in source
    assert "yard_camera_manager.async_refresh" in source


def test_yard_camera_matching_never_falls_back_to_porch_only(component_root):
    source = (component_root / "yard_camera_manager.py").read_text()
    assert "porch_only" not in source
    assert "_base_address(door.address) == base" in source
    assert "Никогда не сопоставляем только по номеру подъезда" in source


def test_yard_stream_resolver_validates_playlist_and_prefers_realtime(component_root):
    source = (component_root / "yard_stream.py").read_text()
    assert '"low_latency", camera.low_latency_hls_url' in source
    assert '"main", camera.hls_url' in source
    assert 'b"#EXTM3U"' in source
    assert "[YARD_STREAM][PROBE]" in source
    assert "url" not in source.split("[YARD_STREAM][PROBE]")[1].split("return response.status", 1)[0]


def test_stream_source_refreshes_expired_media_urls(component_root):
    manager = (component_root / "yard_camera_manager.py").read_text()
    assert "async_stream_source" in manager
    assert "[YARD_STREAM][REFRESH_BEFORE_RETRY]" in manager
    assert "await self.async_refresh()" in manager


def test_yard_stream_uses_local_proxy_and_hides_cdn_bearer(component_root):
    manager = (component_root / "yard_camera_manager.py").read_text()
    init_source = (component_root / "__init__.py").read_text()
    proxy = (component_root / "yard_hls_proxy.py").read_text()
    assert "YardHlsProxy" in manager
    assert "build_stream_url" in manager
    assert "async_setup_yard_hls_proxy" in init_source
    assert 'requires_auth = False' in proxy
    assert '?auth=' in proxy
    utils = (component_root / "yard_hls_utils.py").read_text()
    assert 'inherit_hls_token' in utils
    assert 'resolve_hls_reference' in utils
    assert 'Authorization' in utils
    assert "_rewrite_playlist" in proxy
    assert "looks_like_playlist(prefix)" in proxy
    assert "MEDIA URL" not in proxy


def test_yard_stream_prefers_main_for_ffmpeg_compatibility(component_root):
    source = (component_root / "yard_stream.py").read_text()
    main_pos = source.index('(\"main\", camera.hls_url)')
    low_pos = source.index('(\"low_latency\", camera.low_latency_hls_url)')
    assert main_pos < low_pos
