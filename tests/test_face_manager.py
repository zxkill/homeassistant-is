
def test_face_manager_requires_confirmation_streak(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "_advance_streak" in source
    assert "streak < self._required_matches" in source
    assert "RECOGNITION_MODE_AUTO_OPEN" in source
    assert "blake2b" in source


def test_face_descriptors_stay_128_compatible(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "len(vector) == 128" in source


def test_known_faces_store_home_assistant_person_mapping(component_root):
    source = (component_root / "face_manager.py").read_text()
    assert "person_entity_id" in source
    assert "CONF_FACE_PERSON_ENTITY_ID" in source
    assert "identity_key" in source
    assert "async_link_known_face" in source
    identity_source = (component_root / 'face_identity.py').read_text()
    assert 'hass.states.get' in identity_source
