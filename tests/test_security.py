import importlib.util


def _load_security(component_root):
    spec = importlib.util.spec_from_file_location("intersvyaz_security", component_root / "security.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_recursive_redaction(component_root):
    security = _load_security(component_root)
    payload = {
        "TOKEN": "secret",
        "nested": {"authorization": "Bearer secret", "phone": "+79990000000"},
        "ok": 42,
    }
    safe = security.redact_mapping(payload)
    assert safe["TOKEN"] == security.REDACTED
    assert safe["nested"]["authorization"] == security.REDACTED
    assert safe["nested"]["phone"] == security.REDACTED
    assert safe["ok"] == 42


def test_url_redaction(component_root):
    security = _load_security(component_root)
    safe = security.redact_url("https://secret.example/api/open?a=token")
    assert "secret.example" not in safe
    assert "token" not in safe
