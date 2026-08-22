
def test_services_module_validates_inputs(component_root):
    source = (component_root / "services.py").read_text()
    assert "OPEN_DOOR_SCHEMA" in source
    assert "ADD_FACE_SCHEMA" in source
    assert "REMOVE_FACE_SCHEMA" in source
    assert "_normalize_faces" in source
    assert "SNAPSHOT_MAX_BYTES" in source
