from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_yard_parser_keeps_mse_and_realtime_ws(component_root):
    module = _load_module(
        "intersvyaz_yard_models_test",
        component_root / "yard_models.py",
    )

    groups = module.parse_yard_groups(
        [
            {
                "id": "g1",
                "groupName": "Дом",
                "cameras": [
                    {
                        "ID": 1,
                        "UUID": "ABC",
                        "NAME": "Подъезд",
                        "ADDRESS": "Test",
                        "ACCESS": {
                            "LIVE": {"STATUS": True},
                            "ARCHIVE": {"STATUS": True},
                        },
                        "MEDIA": {
                            "SNAPSHOT": {
                                "LIVE": {
                                    "MAIN": "https://cdn/snapshot.jpg?token=secret"
                                }
                            },
                            "HLS": {
                                "LIVE": {
                                    "MAIN": "https://cdn/live.m3u8?token=secret"
                                }
                            },
                            "MSE": {
                                "LIVE": "wss://cdn/mse/stream?token=secret"
                            },
                        },
                        "REALTIME_WS": {
                            "combined": "wss://cdn/ws/stream?token=secret"
                        },
                    }
                ],
            }
        ]
    )

    camera = groups[0].cameras[0]
    assert camera.mse_url == "wss://cdn/mse/stream?token=secret"
    assert camera.realtime_ws_url == "wss://cdn/ws/stream?token=secret"
    assert camera.hls_url == "https://cdn/live.m3u8?token=secret"


def test_yard_parser_accepts_string_realtime_shapes(component_root):
    module = _load_module(
        "intersvyaz_yard_models_shape_test",
        component_root / "yard_models.py",
    )

    groups = module.parse_yard_groups(
        [
            {
                "cameras": [
                    {
                        "UUID": "ABC",
                        "ACCESS": {"LIVE": {"STATUS": True}},
                        "MEDIA": {
                            "MSE": "https://cdn/mse/stream?x=1",
                        },
                        "REALTIME_WS": "ws://cdn/realtime/stream?x=2",
                    }
                ]
            }
        ]
    )
    camera = groups[0].cameras[0]
    assert camera.mse_url == "https://cdn/mse/stream?x=1"
    assert camera.realtime_ws_url == "ws://cdn/realtime/stream?x=2"


def test_flussonic_source_normalization(component_root):
    package_name = "intersvyaz_realtime_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = [str(component_root)]
    sys.modules[package_name] = package

    _load_module(
        f"{package_name}.models",
        component_root / "models.py",
    )
    realtime = _load_module(
        f"{package_name}.yard_realtime",
        component_root / "yard_realtime.py",
    )

    assert (
        realtime.build_flussonic_source(
            "https://cdn.example/mse/live?token=abc"
        )
        == "flussonic:wss://cdn.example/mse/live?token=abc"
    )
    assert (
        realtime.build_flussonic_source(
            "http://cdn.example/mse/live?token=abc"
        )
        == "flussonic:ws://cdn.example/mse/live?token=abc"
    )
    assert (
        realtime.build_flussonic_source(
            "wss://cdn.example/mse/live?token=abc"
        )
        == "flussonic:wss://cdn.example/mse/live?token=abc"
    )
    assert realtime.build_flussonic_source("rtsp://unsupported/live") is None
