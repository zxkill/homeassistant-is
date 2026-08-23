from __future__ import annotations

import json


def test_dlib_engine_is_isolated_from_home_assistant_process(component_root):
    engine = (component_root / "recognition" / "dlib_engine.py").read_text()
    worker = (component_root / "recognition" / "dlib_worker.py").read_text()

    assert "import dlib" not in engine
    assert "import numpy" not in engine
    assert "from PIL" not in engine
    assert "subprocess.Popen" in engine
    assert "import dlib" in worker
    assert "import numpy as np" in worker
    assert "from PIL import Image, ImageOps" in worker
    assert "dlib.get_frontal_face_detector()" in worker
    assert "dlib.face_recognition_model_v1" in worker


def test_dlib_healthcheck_executes_detector_and_resnet(component_root):
    engine = (component_root / "recognition" / "dlib_engine.py").read_text()
    worker = (component_root / "recognition" / "dlib_worker.py").read_text()

    assert "def probe(self)" in engine
    assert '"command": "healthcheck"' in engine
    assert "simd_probe_ok" in engine
    assert "blank_detector_image" in worker
    assert "_detector(blank_detector_image, 0)" in worker
    assert "blank_face_chip" in worker
    assert "compute_face_descriptor" in worker
    assert '"simd_probe_ok": True' in worker


def test_backend_router_falls_back_on_native_dlib_failure(component_root):
    source = (component_root / "recognition" / "backend_router.py").read_text()

    assert "FaceRecognitionBackendRouter" in source
    assert "PortableFallbackEngine" in source
    assert "self._dlib.fatal_error" in source
    assert "activate_portable" in source
    assert "[FACE][BACKEND_FALLBACK]" in source
    assert "RecognitionBackendSwitched" in source
    assert "[FACE][ENROLL_RETRY]" in source


def test_no_opencv_or_compiled_dlib_source_dependency(component_root):
    manifest = json.loads((component_root / "manifest.json").read_text())
    requirements = [item.lower() for item in manifest["requirements"]]

    assert "dlib-bin==20.0.1" in requirements
    assert not any(item.startswith("dlib==") for item in requirements)
    assert not any("opencv" in item for item in requirements)
    assert not any(item.startswith("face-recognition") for item in requirements)


def test_models_are_downloaded_automatically_and_verified(component_root):
    source = (component_root / "recognition" / "model_manager.py").read_text()

    assert "github.com/davisking/dlib-models" in source
    assert "6e787bbebf5c9efdb793f6cd1f023230c4413306605f24f299f12869f95aa472" in source
    assert "abb1f61041e434465855ce81c2bd546e830d28bcbed8d27ffbe5bb408b11553a" in source
    assert "hashlib.sha256" in source
    assert "bz2.decompress" in source
    assert "_MODEL_LOCK = asyncio.Lock()" in source


def test_auto_open_is_stricter_for_dlib(component_root):
    const = (component_root / "const.py").read_text()
    engine = (component_root / "recognition" / "dlib_engine.py").read_text()
    worker = (component_root / "recognition" / "dlib_worker.py").read_text()

    assert "FACE_RECOGNITION_DISTANCE_THRESHOLD = 0.52" in const
    assert "FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX = 0.50" in const
    assert '"auto_open_threshold": FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX' in engine
    assert "best_distance <= auto_open_threshold" in worker
    assert "len(faces) == 1" in worker


def test_face_manager_closes_both_backends_on_unload(component_root):
    router = (component_root / "recognition" / "backend_router.py").read_text()
    manager = (component_root / "face_manager.py").read_text()
    assert "self._dlib.close()" in router
    assert "self._portable.close()" in router
    assert "self._engine.close" in manager
