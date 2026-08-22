
def test_local_engine_is_lazy_and_dlib_based(component_root):
    source = (component_root / "recognition" / "engine.py").read_text()
    assert "dlib" in source
    assert "face_recognition_models" in source
    assert "extract_single_encoding" in source
    assert "recognize" in source
    assert "import pkg_resources" not in source
