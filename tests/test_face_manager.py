def test_face_manager_requires_confirmation_streak(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "_advance_streak" in source
    assert "streak < self._required_matches" in source
    assert "RECOGNITION_MODE_AUTO_OPEN" in source
    assert "blake2b" in source


def test_face_descriptors_stay_engine_tagged(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "len(face.encoding) == 128" in source
    assert "len(vector) == 128" in source
    assert "FACE_ENGINE_DLIB_RESNET_V1" in source
    assert "FACE_ENGINE_PORTABLE_V1" in source


def test_known_faces_store_home_assistant_person_mapping(component_root):
    source = (component_root / "face_manager.py").read_text()
    identity_source = (component_root / "face_identity.py").read_text()
    assert "person_entity_id" in source
    assert "CONF_FACE_PERSON_ENTITY_ID" in source
    assert "identity_key" in source
    assert "async_link_known_face" in source
    assert "hass.states.get" in identity_source


def test_multiple_templates_per_person_are_supported_per_engine(component_root):
    source = (component_root / "face_manager.py").read_text()
    const = (component_root / "const.py").read_text()
    assert "FACE_TEMPLATES_PER_PERSON_MAX = 5" in const
    assert "_trim_templates" in source
    assert "engine_id" in source
    assert "identity_templates" in source
    assert "list_known_face_names" in source


def test_old_portable_descriptors_are_restored_for_cpu_fallback(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "engine not in" in source
    assert "FACE_ENGINE_DLIB_RESNET_V1" in source
    assert "FACE_ENGINE_PORTABLE_V1" in source
    assert "portable_templates" in source
    assert "[FACE][LOAD_SKIP]" in source


def test_backend_switch_retries_recognition_with_correct_descriptor_set(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "RecognitionBackendSwitched" in source
    assert "_async_recognize_with_retry" in source
    # 3 attempts: a single frame may need two consecutive switches in the
    # worst case (remote -> dlib -> portable).
    assert "for attempt in range(3)" in source
    assert "face.engine == engine_id" in source
    assert "[FACE][ANALYZE_RETRY]" in source


def test_fallback_suspends_auto_open_without_rewriting_options(component_root):
    """A transient remote outage must not flip the user's saved mode to observe."""
    source = (component_root / "face_manager.py").read_text(encoding="utf-8")
    const = (component_root / "const.py").read_text(encoding="utf-8")
    assert "FACE_PORTABLE_DISTANCE_THRESHOLD = 0.30" in const
    assert "[FACE][FALLBACK_SAFETY]" in source

    safety = source[
        source.index("async def _async_apply_fallback_safety"):
        source.index("def _trim_templates")
    ]
    assert "async_update_entry" not in safety
    assert "CONF_RECOGNITION_MODE" not in safety
    assert "self._fallback_suppress_reason = reason" in safety

    # Auto-open is blocked only while portable is active and resumes by itself.
    assert "reason=portable_fallback" in source
    assert "self._engine_id() != FACE_ENGINE_PORTABLE_V1" in source
    assert "[FACE][FALLBACK_SAFETY_CLEARED]" in source
    # Portable in fallback uses its safe default threshold, in memory only.
    assert "return FACE_PORTABLE_DISTANCE_THRESHOLD" in source


def test_options_flow_auto_open_confirms_portable(component_root):
    source = (component_root / "options_flow.py").read_text(encoding="utf-8")
    assert "confirm_portable_auto_open()" in source
