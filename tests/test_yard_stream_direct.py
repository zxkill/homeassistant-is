def test_yard_stream_uses_main_with_minimal_ffmpeg_compat(component_root):
    manager = (component_root / "yard_camera_manager.py").read_text()
    resolver = (component_root / "yard_stream.py").read_text()
    compat = (component_root / "yard_hls_compat.py").read_text()
    init_source = (component_root / "__init__.py").read_text()

    assert "YardHlsCompatProxy" in manager
    assert "build_stream_url" in manager
    assert "[YARD_STREAM][COMPAT_SOURCE]" in manager
    assert "async_setup_yard_hls_compat" in init_source

    assert 'candidates = (("main", camera.hls_url),)' in resolver
    assert "LOW_LATENCY_ONLY" in resolver
    assert "camera.low_latency_hls_url" in resolver

    assert "RollingPlaylistState" not in compat
    assert "adapt_single_segment_live_playlist" not in compat
    assert "#EXT-X-ENDLIST" not in compat
    assert '"Authorization"' not in compat
    assert '"Origin"' not in compat
    assert '"Referer"' not in compat
