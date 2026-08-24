def test_yard_manager_prefers_realtime_only_when_requested(component_root):
    source = (component_root / "yard_camera_manager.py").read_text()

    assert "prefer_realtime" in source
    assert "build_realtime_source" in source
    assert "[YARD_STREAM][SELECT]" in source
    assert "mode=realtime_flussonic" in source
    assert "_async_hls_stream_source" in source
    assert "[YARD_STREAM][REALTIME_FALLBACK]" in source


def test_yard_manager_refreshes_signed_realtime_urls_on_demand(component_root):
    source = (component_root / "yard_camera_manager.py").read_text()
    const = (component_root / "const.py").read_text()

    assert "YARD_REALTIME_REFRESH_SECONDS = 90" in const
    assert "_last_media_refresh_monotonic" in source
    assert "_realtime_refresh_lock" in source
    assert "_async_refresh_realtime_if_stale" in source
    assert "[YARD_STREAM][REALTIME_REFRESH]" in source
    assert "asyncio.Lock()" in source


def test_yard_manager_never_logs_realtime_urls(component_root):
    source = (component_root / "yard_camera_manager.py").read_text()

    # Logging uses hashed camera references and transport names only.
    assert "_safe_camera_ref" in source
    assert "realtime_source," not in source
    assert "camera.mse_url," not in source
    assert "camera.realtime_ws_url," not in source
