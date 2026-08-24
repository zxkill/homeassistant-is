def test_camera_uses_shared_runtime_and_dual_stream_paths(component_root):
    source = (component_root / "camera.py").read_text()

    assert "snapshot_manager" in source
    assert "yard_camera_manager.async_refresh" in source
    assert "door_manager.async_refresh" in source
    assert "stream_source" in source
    assert "hass.data" not in source

    # Realtime is only exposed to a provider that explicitly supports it.
    assert "provider.async_is_supported" in source
    assert "realtime_flussonic" in source

    # HLS fallback must never accidentally receive a flussonic: source.
    assert "ContextVar" in source
    assert "_FORCE_HLS_SOURCE" in source
    assert "async def async_create_stream" in source
    assert "await super().async_create_stream()" in source

    # Extra FFmpeg/HLS compatibility from the reference implementation.
    assert '_attr_stream_options = {"allowed_extensions": "ALL"}' in source
