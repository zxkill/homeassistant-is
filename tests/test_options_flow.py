
def test_options_flow_contains_safe_modes(component_root):
    source = (component_root / "options_flow.py").read_text()
    assert "recognition_settings" in source
    assert "RECOGNITION_MODE_OFF" in source
    assert "RECOGNITION_MODE_OBSERVE" in source
    assert "RECOGNITION_MODE_AUTO_OPEN" in source
    assert "FileSelector" in source
    assert "process_uploaded_file" in source

    assert "_recognition_mode_label" in source
