
def test_each_door_has_status_and_last_visitor(component_root):
    source = (component_root / "sensor.py").read_text()
    assert "IntersvyazDoorStatusSensor" in source
    assert "IntersvyazLastVisitorSensor" in source
    assert "door_device_info" in source


def test_profile_does_not_expose_phone_attribute(component_root):
    source = (component_root / "sensor.py").read_text()
    profile = source[source.index("class IntersvyazProfileSensor"):source.index("class IntersvyazDoorStatusSensor")]
    assert '"phone"' not in profile
