
def test_options_flow_contains_safe_modes(component_root):
    source = (component_root / "options_flow.py").read_text()
    assert "recognition_settings" in source
    assert "RECOGNITION_MODE_OFF" in source
    assert "RECOGNITION_MODE_OBSERVE" in source
    assert "RECOGNITION_MODE_AUTO_OPEN" in source
    assert "FileSelector" in source
    assert "process_uploaded_file" in source

    assert "_recognition_mode_label" in source


def test_options_flow_links_faces_to_home_assistant_people(component_root):
    source = (component_root / "options_flow.py").read_text()
    assert 'EntitySelectorConfig(domain="person")' in source
    assert "async_step_link_face" in source
    assert "async_link_known_face" in source
    assert "CONF_FACE_PERSON_ENTITY_ID" in source
