def test_old_multicrop_engine_is_no_longer_active(component_root):
    package = (component_root / "recognition" / "__init__.py").read_text()
    assert "dlib_engine" in package
    assert "engine_v2" not in package
    assert "worker_v2" not in package


def test_dlib_engine_keeps_detailed_compare_logging(component_root):
    engine = (component_root / "recognition" / "dlib_engine.py").read_text()
    assert "[FACE][COMPARE]" in engine
    assert "known_templates" in engine
    assert "detector_score" in engine
    assert "face_side" in engine
    assert "auto_open_safe" in engine
