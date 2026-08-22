"""Проверки метаданных integration package Intersvyaz 2.0."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "intersvyaz"


def test_manifest_v2_metadata() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["domain"] == "intersvyaz"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "hub"
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["version"] == "2.0.0-beta.1"
    assert "dlib-bin==20.0.1" in manifest["requirements"]
    assert "face-recognition-models==0.3.0" in manifest["requirements"]


def test_hacs_metadata_still_points_to_domain() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert "intersvyaz" in hacs["domains"]
    assert hacs["content_in_root"] is False
