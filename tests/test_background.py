def test_background_uses_shared_snapshot_manager(component_root):
    source = (component_root / "background.py").read_text()
    assert "snapshot_manager" in source
    assert "face_manager" in source
    assert "yard_camera_manager" in source
    assert "hass.data" not in source
