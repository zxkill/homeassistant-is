from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _worker_rpc(worker: Path, request: dict) -> dict:
    process = subprocess.Popen(
        [sys.executable, "-u", str(worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        process.stdin.write(json.dumps({"command": "shutdown", "request_id": -1}) + "\n")
        process.stdin.flush()
        process.wait(timeout=5)
        return response
    finally:
        if process.poll() is None:
            process.kill()


def _load_worker_v2(component_root: Path, monkeypatch: pytest.MonkeyPatch):
    recognition_root = component_root / "recognition"
    monkeypatch.syspath_prepend(str(recognition_root))
    path = recognition_root / "worker_v2.py"
    spec = importlib.util.spec_from_file_location("intersvyaz_worker_v2_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_multicrop_variants_are_conservative_and_bounded(component_root, monkeypatch):
    worker = _load_worker_v2(component_root, monkeypatch)

    variants = worker._variant_boxes((100, 80, 180, 180), 320, 240)

    assert len(variants) == 6
    assert (100, 80, 180, 180) in variants
    assert len(set(variants)) == len(variants)
    assert all(0 <= left < right <= 320 for left, _top, right, _bottom in variants)
    assert all(0 <= top < bottom <= 240 for _left, top, _right, bottom in variants)


def test_multicrop_worker_healthcheck(component_root):
    if importlib.util.find_spec("numpy") is None or importlib.util.find_spec("PIL") is None:
        pytest.skip("Pillow/NumPy not installed in unit-test environment")

    response = _worker_rpc(
        component_root / "recognition" / "worker_v2.py",
        {"command": "healthcheck", "request_id": 1},
    )

    assert response["ok"] is True
    assert response["engine"] == "portable_face_v1"
    assert response["matcher"] == "portable_multicrop_v2"
    assert response["crop_variants"] == 6


def test_multicrop_keeps_strict_threshold_and_existing_descriptors(component_root):
    worker = (component_root / "recognition" / "worker_v2.py").read_text()
    engine = (component_root / "recognition" / "engine_v2.py").read_text()
    package = (component_root / "recognition" / "__init__.py").read_text()

    assert "threshold = max(0.10, min(0.55, threshold))" in worker
    assert 'engine_id = "portable_face_v1"' in engine
    assert "[FACE][COMPARE]" in engine
    assert "from .engine_v2 import" in package
