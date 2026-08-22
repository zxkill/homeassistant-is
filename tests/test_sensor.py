
def test_each_door_has_status_and_last_visitor(component_root):
    source = (component_root / "sensor.py").read_text()
    assert "IntersvyazDoorStatusSensor" in source
    assert "IntersvyazLastVisitorSensor" in source
    assert "door_device_info" in source


def test_profile_does_not_expose_phone_attribute(component_root):
    source = (component_root / "sensor.py").read_text()
    profile = source[source.index("class IntersvyazProfileSensor"):source.index("class IntersvyazDoorStatusSensor")]
    assert '"phone"' not in profile


def test_monetary_sensor_uses_iso_currency_string(component_root):
    """HA 2026.8 expects ISO 4217 string; UnitOfCurrency is not exported."""
    source = (component_root / "sensor.py").read_text()
    assert "UnitOfCurrency" not in source
    assert '_attr_native_unit_of_measurement = "RUB"' in source


def test_last_visitor_exposes_person_entity_id(component_root):
    source = (component_root / "sensor.py").read_text()
    assert '"person_entity_id"' in source
