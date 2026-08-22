def test_yard_stream_uses_direct_main_without_proxy(component_root):
    manager = (component_root / "yard_camera_manager.py").read_text()
    resolver = (component_root / "yard_stream.py").read_text()
    init_source = (component_root / "__init__.py").read_text()

    assert "return source" in manager
    assert "build_stream_url" not in manager
    assert "YardHlsProxy" not in manager
    assert "async_setup_yard_hls_proxy" not in init_source

    assert 'candidates = (("main", camera.hls_url),)' in resolver
    assert "camera.low_latency_hls_url" in resolver
    assert "LOW_LATENCY_ONLY" in resolver
    assert "build_upstream_headers" not in resolver
    assert '"Authorization"' not in resolver
    assert '"Origin"' not in resolver
    assert '"Referer"' not in resolver
