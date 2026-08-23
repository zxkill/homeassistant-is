def test_face_manager_requires_confirmation_streak(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "_advance_streak" in source
    assert "streak < self._required_matches" in source
    assert "RECOGNITION_MODE_AUTO_OPEN" in source
    assert "blake2b" in source


def test_face_descriptors_stay_128_compatible(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "len(face.encoding) == 128" in source
    assert "len(vector) == 128" in source
    assert "FACE_ENGINE_DLIB_RESNET_V1" in source


def test_known_faces_store_home_assistant_person_mapping(component_root):
    source = (component_root / "face_manager.py").read_text()
    identity_source = (component_root / "face_identity.py").read_text()
    assert "person_entity_id" in source
    assert "CONF_FACE_PERSON_ENTITY_ID" in source
    assert "identity_key" in source
    assert "async_link_known_face" in source
    assert "hass.states.get" in identity_source


def test_multiple_templates_per_person_are_supported(component_root):
    source = (component_root / "face_manager.py").read_text()
    const = (component_root / "const.py").read_text()
    assert "FACE_TEMPLATES_PER_PERSON_MAX = 5" in const
    assert "_trim_templates" in source
    assert "identity_templates" in source
    assert "list_known_face_names" in source
    assert "seen: set[str]" in source


def test_old_portable_descriptors_are_never_mixed_with_dlib(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "engine != FACE_ENGINE_DLIB_RESNET_V1" in source
    assert "[FACE][MIGRATION_SKIP]" in source
    assert "_LEGACY_PORTABLE_DEFAULT_THRESHOLD = 0.30" in source
    assert "[FACE][MIGRATION_THRESHOLD]" in source
    assert "FACE_RECOGNITION_DISTANCE_THRESHOLD" in source


def test_first_dlib_enrollment_forces_safe_observe_mode(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "force_observe_after_enroll" in source
    assert "not self._known_faces" in source
    assert "[FACE][MIGRATION_AUTO_OPEN_DISABLED]" in source
    assert "new_recognition_engine_requires_observation" in source
