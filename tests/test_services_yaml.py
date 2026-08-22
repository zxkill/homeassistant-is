import yaml


def test_service_descriptions_are_complete(component_root):
    data = yaml.safe_load((component_root / "services.yaml").read_text())
    assert set(data) == {"open_door", "add_known_face", "remove_known_face"}
    for name, definition in data.items():
        assert definition.get("name")
        assert definition.get("description")
        assert "entry_id" in definition.get("fields", {})
