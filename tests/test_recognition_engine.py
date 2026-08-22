from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

import pytest


def test_local_engine_is_isolated_from_home_assistant_process(component_root):
    engine = (component_root / "recognition" / "engine.py").read_text()
    worker = (component_root / "recognition" / "worker.py").read_text()

    # Native OpenCV/numpy must never be imported by the Home Assistant process.
    assert "import cv2" not in engine
    assert "import numpy" not in engine
    assert "subprocess.Popen" in engine
    assert "SIGILL" in engine

    # Native dependencies are isolated in a child worker process.
    assert "import cv2" in worker
    assert "import numpy as np" in worker
    assert "haarcascade_frontalface_default.xml" in worker
    assert "_lbp_descriptor" in worker
    assert "extract_single_encoding" in worker
    assert "recognize" in worker


def test_worker_uses_low_resource_opencv_settings(component_root):
    worker = (component_root / "recognition" / "worker.py").read_text()
    assert "cv2.setNumThreads(1)" in worker
    assert "cv2.ocl.setUseOpenCL(False)" in worker
    assert "healthcheck" in worker


def test_face_manager_closes_worker_on_unload(component_root):
    face_manager = (component_root / "face_manager.py").read_text()
    integration = (component_root / "__init__.py").read_text()

    assert "async def async_stop" in face_manager
    assert "self._engine.close" in face_manager
    assert "await runtime.face_manager.async_stop()" in integration


def test_face_manager_requires_confirmation_streak(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "_advance_streak" in source
    assert "streak < self._required_matches" in source
    assert "RECOGNITION_MODE_AUTO_OPEN" in source
    assert "blake2b" in source


def test_face_descriptors_are_engine_tagged(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "len(vector) == 128" in source
    assert "CONF_FACE_ENGINE" in source
    assert "FACE_ENGINE_OPENCV_LBP_V1" in source
    assert "descriptor создан старым движком" in source


@pytest.mark.skipif(importlib.util.find_spec("cv2") is None, reason="OpenCV not installed in unit-test environment")
def test_opencv_worker_healthcheck(component_root):
    worker = component_root / "recognition" / "worker.py"
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
        process.stdin.write(json.dumps({"command": "healthcheck", "request_id": 1}) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        assert response["ok"] is True
        assert response["engine"] == "opencv_lbp_v1"
        assert response["descriptor_size"] == 128
    finally:
        if process.poll() is None:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"command": "shutdown", "request_id": 2}) + "\n")
            process.stdin.flush()
            process.wait(timeout=5)
