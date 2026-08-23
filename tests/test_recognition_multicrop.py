def test_portable_multicrop_is_runtime_fallback(component_root):
    router = (component_root / "recognition" / "backend_router.py").read_text()
    portable = (component_root / "recognition" / "engine_v2.py").read_text()

    assert "engine_v2" in router
    assert "PortableFallbackEngine" in router
    assert 'engine_id = "portable_face_v1"' in portable
    assert "portable_multicrop" in router


def test_dlib_and_portable_keep_detailed_compare_logging(component_root):
    dlib = (component_root / "recognition" / "dlib_engine.py").read_text()
    portable = (component_root / "recognition" / "engine_v2.py").read_text()
    assert "[FACE][COMPARE]" in dlib
    assert "[FACE][COMPARE]" in portable
    assert "known_templates" in dlib
    assert "variants_tested" in portable
