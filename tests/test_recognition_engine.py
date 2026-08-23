from __future__ import annotations

import json


def test_dlib_engine_is_isolated_from_home_assistant_process(component_root):
    engine = (component_root / "recognition" / "dlib_engine.py").read_text()
    worker = (component_root / "recognition" / "dlib_worker.py").read_text()
    package = (component_root / "recognition" / "__init__.py").read_text()

    assert "import dlib" not in engine
    assert "import numpy" not in engine
    assert "from PIL" not in engine
    assert "subprocess.Popen" in engine
    assert "import dlib" in worker
    assert "import numpy as np" in worker
    assert "from PIL import Image, ImageOps" in worker
    assert "dlib.get_frontal_face_detector()" in worker
    assert "dlib.shape_predictor" in worker
    assert "dlib.face_recognition_model_v1" in worker
    assert "compute_face_descriptor" in worker
    assert "from .dlib_engine import" in package
    assert "from .engine_v2 import" not in package


def test_dlib_worker_uses_real_resnet_embeddings(component_root):
    worker = (component_root / "recognition" / "dlib_worker.py").read_text()

    assert '_ENGINE_ID = "dlib_resnet_v1"' in worker
    assert "_DESCRIPTOR_SIZE = 128" in worker
    assert "shape_predictor_5_face_landmarks.dat" in worker
    assert "dlib_face_recognition_resnet_model_v1.dat" in worker
    assert "np.linalg.norm(known - candidate)" in worker
    assert "num_jitters=2" in worker
    assert "num_jitters=1" in worker
    assert "_skin_mask" not in worker
    assert "_descriptor(" in worker


def test_no_opencv_or_compiled_dlib_source_dependency(component_root):
    manifest = json.loads((component_root / "manifest.json").read_text())
    requirements = [item.lower() for item in manifest["requirements"]]

    assert "dlib-bin==20.0.1" in requirements
    assert not any(item.startswith("dlib==") for item in requirements)
    assert not any("opencv" in item for item in requirements)
    assert not any(item.startswith("face-recognition") for item in requirements)


def test_models_are_downloaded_automatically_and_verified(component_root):
    source = (component_root / "recognition" / "model_manager.py").read_text()

    assert "https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2" in source
    assert "https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2" in source
    assert "6e787bbebf5c9efdb793f6cd1f023230c4413306605f24f299f12869f95aa472" in source
    assert "abb1f61041e434465855ce81c2bd546e830d28bcbed8d27ffbe5bb408b11553a" in source
    assert "hashlib.sha256" in source
    assert "bz2.decompress" in source
    assert "[FACE][MODEL_DOWNLOAD_BEGIN]" in source
    assert "[FACE][MODEL_DOWNLOAD_OK]" in source
    assert "[FACE][MODEL_HASH_MISMATCH]" in source
    assert "_MODEL_LOCK = asyncio.Lock()" in source
    assert "async with _MODEL_LOCK" in source


def test_auto_open_is_stricter_than_observation(component_root):
    const = (component_root / "const.py").read_text()
    engine = (component_root / "recognition" / "dlib_engine.py").read_text()
    worker = (component_root / "recognition" / "dlib_worker.py").read_text()

    assert "FACE_RECOGNITION_DISTANCE_THRESHOLD = 0.52" in const
    assert "FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX = 0.50" in const
    assert '"auto_open_threshold": FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX' in engine
    assert "best_distance <= auto_open_threshold" in worker
    assert "len(faces) == 1" in worker


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
