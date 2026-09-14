def test_remote_engine_is_encoder_only_and_never_marks_itself_fatal(component_root):
    source = (component_root / "recognition" / "remote_engine.py").read_text()

    assert 'engine_id = "remote_dlib_resnet_v1"' in source
    assert "class RemoteRecognitionUnavailable(HomeAssistantError)" in source
    assert "class RemoteRecognitionAuthError(HomeAssistantError)" in source
    # The router (not this client) owns the transient-cooldown decision.
    assert "def fatal_error(self) -> str | None:\n        # A remote failure never permanently disables this engine" in source
    assert "/v1/encode/single" in source
    assert "/v1/encode/multi" in source
    assert "/health" in source
    # No known-faces library on the wire: the service only ever gets images.
    assert "run_coroutine_threadsafe" in source


def test_backend_router_prefers_remote_with_transient_cooldown(component_root):
    source = (component_root / "recognition" / "backend_router.py").read_text()

    assert "FACE_ENGINE_REMOTE_DLIB_V1" not in source  # router talks via engine_id, not the const directly
    assert "RemoteFaceRecognitionEngine" in source
    assert "RemoteRecognitionUnavailable" in source
    assert "RemoteRecognitionAuthError" in source
    assert "def configure_remote(" in source
    assert "def refresh_active_backend(" in source
    assert "using_remote" in source
    assert "_dlib_permanently_disabled" in source
    assert "_REMOTE_COOLDOWN_INITIAL_SECONDS" in source
    assert "_REMOTE_COOLDOWN_MAX_SECONDS" in source
    assert "[FACE][%s] from=remote_dlib" in source
    assert '"REMOTE_AUTH_FAILED" if is_auth_error else "REMOTE_FALLBACK"' in source
    assert "[FACE][REMOTE_RETRY]" in source
    # dlib -> portable stays permanent; remote failures do not use activate_portable.
    assert "activate_portable" in source


def test_face_manager_skips_dlib_probe_while_remote_is_healthy(component_root):
    source = (component_root / "face_manager.py").read_text()

    assert "self._engine.refresh_active_backend()" in source
    assert "self._engine.using_remote" in source
    assert "FACE_ENGINE_REMOTE_DLIB_V1" in source
    assert "self._remote_threshold" in source
    assert "self._engine.configure_remote(" in source
    # Enrollment/recognition fallback safety only ever targets the portable engine.
    assert "backend_after == FACE_ENGINE_PORTABLE_V1" in source
    assert 'self._engine_id() == FACE_ENGINE_PORTABLE_V1' in source


def test_options_flow_exposes_remote_recognition_step(component_root):
    source = (component_root / "options_flow.py").read_text()

    assert "async_step_remote_recognition" in source
    assert "CONF_REMOTE_RECOGNITION_URL" in source
    assert "CONF_REMOTE_RECOGNITION_API_KEY" in source
    assert "_async_check_remote_health" in source
    assert "remote_connection_failed" in source
