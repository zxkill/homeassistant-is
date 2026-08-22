
def test_runtime_data_architecture(component_root):
    source = (component_root / "__init__.py").read_text()
    assert "entry.runtime_data = runtime" in source
    assert "hass.data.setdefault" not in source
    assert "Platform.EVENT" in source
    assert "async_migrate_entry" in source


def test_no_config_entry_update_listener(component_root):
    source = (component_root / "__init__.py").read_text()
    assert "add_update_listener" not in source
