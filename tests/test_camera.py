def test_camera_uses_shared_runtime(component_root):
    source = (component_root / "camera.py").read_text()
    assert "snapshot_manager" in source
    assert "yard_camera_manager.async_refresh" in source
    assert "door_manager.async_refresh" in source
    assert "stream_source" in source
    assert "hass.data" not in source
