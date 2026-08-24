def test_diagnostics_exposes_only_realtime_capabilities(component_root):
    source = (component_root / "diagnostics.py").read_text()

    assert '"has_mse_url": bool(camera.mse_url)' in source
    assert '"has_realtime_ws_url": bool(camera.realtime_ws_url)' in source
    assert '"yard_realtime_camera_count"' in source
    assert '"mse_url": camera.mse_url' not in source
    assert '"realtime_ws_url": camera.realtime_ws_url' not in source
