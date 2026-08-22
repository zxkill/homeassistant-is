from pathlib import Path


def test_local_engine_is_isolated_from_home_assistant_process(component_root):
    engine = (component_root / "recognition" / "engine.py").read_text()
    worker = (component_root / "recognition" / "worker.py").read_text()

    # Native dlib must never be imported by the Home Assistant process.
    assert "import dlib" not in engine
    assert "subprocess.Popen" in engine
    assert "SIGILL" in engine

    # Native dependencies are isolated in a child worker process.
    assert "import dlib" in worker
    assert "import numpy as np" in worker
    assert "from PIL import Image" in worker
    assert "extract_single_encoding" in worker
    assert "recognize" in worker


def test_worker_does_not_use_pkg_resources(component_root):
    worker = (component_root / "recognition" / "worker.py").read_text()
    assert "import pkg_resources" not in worker
    assert 'find_spec("face_recognition_models")' in worker


def test_face_manager_closes_worker_on_unload(component_root):
    face_manager = (component_root / "face_manager.py").read_text()
    integration = (component_root / "__init__.py").read_text()

    assert "async def async_stop" in face_manager
    assert "self._engine.close" in face_manager
    assert "await runtime.face_manager.async_stop()" in integration
