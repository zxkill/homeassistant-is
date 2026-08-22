
def test_services_module_validates_inputs(component_root):
    source = (component_root / "services.py").read_text()
    assert "OPEN_DOOR_SCHEMA" in source
    assert "ADD_FACE_SCHEMA" in source
    assert "REMOVE_FACE_SCHEMA" in source
    assert "_normalize_faces" in source
    assert "SNAPSHOT_MAX_BYTES" in source


def test_services_support_person_entity_mapping(component_root):
    source = (component_root / "services.py").read_text()
    assert 'vol.Optional("person_entity_id")' in source
    assert 'person_entity_id=(' in source
    yaml_source = (component_root / "services.yaml").read_text()
    assert "person_entity_id:" in yaml_source
    assert "domain: person" in yaml_source
