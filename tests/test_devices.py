def test_device_info_does_not_use_deprecated_via_device(component_root):
    """HA 2026.9 rejects `via_device` tuples for entities added from background tasks."""
    source = (component_root / "devices.py").read_text(encoding="utf-8")
    assert "via_device=" not in source
    assert "via_device_id" in source


def test_hub_device_registered_before_platforms(component_root):
    source = (component_root / "__init__.py").read_text(encoding="utf-8")
    register = source.index("register_hub_device(hass, entry.entry_id)")
    forward = source.index("async_forward_entry_setups(entry, PLATFORMS)")
    assert register < forward
    assert "forget_hub_device(entry.entry_id)" in source
