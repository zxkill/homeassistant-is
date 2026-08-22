from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _worker_rpc(worker: Path, requests: list[dict]) -> list[dict]:
    process = subprocess.Popen(
        [sys.executable, "-u", str(worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    responses: list[dict] = []
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            responses.append(json.loads(process.stdout.readline()))
        process.stdin.write(json.dumps({"command": "shutdown", "request_id": -1}) + "\n")
        process.stdin.flush()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
    return responses


def test_local_engine_is_isolated_from_home_assistant_process(component_root):
    engine = (component_root / "recognition" / "engine.py").read_text()
    worker = (component_root / "recognition" / "worker.py").read_text()

    # Heavy image math stays in the child worker; HA imports neither NumPy nor cv2 here.
    assert "import cv2" not in engine
    assert "import numpy" not in engine
    assert "from PIL" not in engine
    assert "subprocess.Popen" in engine
    assert "SIGILL" in engine

    assert "import cv2" not in worker
    assert "import dlib" not in worker
    assert "import numpy as np" in worker
    assert "from PIL import" in worker
    assert "_skin_mask" in worker
    assert "_descriptor" in worker
    assert "extract_single_encoding" in worker
    assert "recognize" in worker


def test_portable_worker_healthcheck(component_root):
    if importlib.util.find_spec("numpy") is None or importlib.util.find_spec("PIL") is None:
        pytest.skip("Pillow/NumPy not installed in unit-test environment")

    worker = component_root / "recognition" / "worker.py"
    response = _worker_rpc(
        worker,
        [{"command": "healthcheck", "request_id": 1}],
    )[0]
    assert response["ok"] is True
    assert response["engine"] == "portable_face_v1"
    assert response["descriptor_size"] == 128
    assert response["detector"] == "portable_skin_components_v1"


def test_worker_has_no_opencv_runtime_dependency(component_root):
    worker = (component_root / "recognition" / "worker.py").read_text()
    manifest = json.loads((component_root / "manifest.json").read_text())

    assert "import cv2" not in worker
    assert "from cv2" not in worker
    assert all("opencv" not in requirement.lower() for requirement in manifest["requirements"])
    assert all("dlib" not in requirement.lower() for requirement in manifest["requirements"])
    assert manifest["requirements"] == ["numpy==2.3.2"]


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
    assert "result.auto_open_safe" in source
    assert "blake2b" in source


def test_face_descriptors_are_engine_tagged(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "len(vector) == 128" in source
    assert "CONF_FACE_ENGINE" in source
    assert "FACE_ENGINE_PORTABLE_V1" in source
    assert "descriptor создан несовместимым движком" in source


def test_portable_worker_real_face_smoke(component_root, repo_root):
    """If the repository smoke image is available, enroll and recognize it end-to-end."""

    if importlib.util.find_spec("numpy") is None or importlib.util.find_spec("PIL") is None:
        pytest.skip("Pillow/NumPy not installed in unit-test environment")

    # During local generation these files live next to the repo. CI may not have them,
    # so this remains an opportunistic integration smoke test rather than a fixture.
    candidates = [
        Path("/mnt/data/recognition_test_astronaut.jpg"),
        Path("/mnt/data/astronaut.jpg"),
    ]
    image_path = next((path for path in candidates if path.is_file()), None)
    if image_path is None:
        pytest.skip("Recognition smoke image is not available")

    flipped_candidates = [
        Path("/mnt/data/recognition_test_astronaut_flip.jpg"),
        Path("/mnt/data/astronaut_flip.jpg"),
    ]
    flipped_path = next((path for path in flipped_candidates if path.is_file()), None)
    if flipped_path is None:
        pytest.skip("Flipped recognition smoke image is not available")

    worker = component_root / "recognition" / "worker.py"
    image = base64.b64encode(image_path.read_bytes()).decode("ascii")
    extract = _worker_rpc(
        worker,
        [{"command": "extract_single_encoding", "request_id": 1, "image": image}],
    )[0]
    assert extract["ok"] is True
    assert len(extract["encoding"]) == 128

    flipped = base64.b64encode(flipped_path.read_bytes()).decode("ascii")
    recognize = _worker_rpc(
        worker,
        [
            {
                "command": "recognize",
                "request_id": 2,
                "image": flipped,
                "known_faces": [{"name": "Astronaut", "encoding": extract["encoding"]}],
                "threshold": 0.30,
            }
        ],
    )[0]
    assert recognize["ok"] is True
    assert recognize["matched_name"] == "Astronaut"
    assert recognize["faces_detected"] == 1
    assert recognize["distance"] <= 0.30
    assert recognize["auto_open_safe"] is True


def test_portable_threshold_ui_matches_engine(component_root):
    options_flow = (component_root / "options_flow.py").read_text()
    strings = (component_root / "strings.json").read_text()
    ru = (component_root / "translations" / "ru.json").read_text()

    assert "min=0.10" in options_flow
    assert "max=0.55" in options_flow
    assert "OpenCV/LBP distance" not in strings
    assert "OpenCV/LBP" not in ru


def test_portable_upgrade_forces_safe_migration(component_root):
    config_flow = (component_root / "config_flow.py").read_text()
    integration = (component_root / "__init__.py").read_text()

    assert "VERSION = 4" in config_flow
    assert "target_version = 4" in integration
    assert "RECOGNITION_MODE_OBSERVE" in integration
    assert "[MIGRATION][AUTO_OPEN_DISABLED]" in integration
    assert "FACE_RECOGNITION_DISTANCE_THRESHOLD" in integration
    assert "FACE_REQUIRED_MATCHES_DEFAULT" in integration
