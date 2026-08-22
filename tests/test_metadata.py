import json


def test_manifest_is_stable_v2(component_root):
    manifest = json.loads((component_root / "manifest.json").read_text())
    assert manifest["domain"] == "intersvyaz"
    assert manifest["version"] == "2.0.4"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "hub"
    assert "dlib-bin==20.0.1" in manifest["requirements"]
    assert "face-recognition-models==0.3.0" in manifest["requirements"]
    assert "numpy==2.3.2" in manifest["requirements"]
    assert "Pillow==12.3.0" in manifest["requirements"]


def test_hacs_metadata(repo_root):
    hacs = json.loads((repo_root / "hacs.json").read_text())
    assert hacs["domains"] == ["intersvyaz"]
    assert hacs["homeassistant"] == "2026.8.0"


def test_translation_json(component_root):
    for path in [
        component_root / "strings.json",
        component_root / "translations" / "ru.json",
        component_root / "translations" / "en.json",
    ]:
        data = json.loads(path.read_text())
        assert "config" in data
        assert "options" in data
        assert "reauth_confirm" in data["config"]["step"]
        assert "reconfigure" in data["config"]["step"]


def test_options_menu_uses_current_translation_schema(component_root):
    """Options menu labels must use the current HA menu_options schema."""

    expected = {
        "recognition_settings",
        "add_face",
        "remove_face",
        "background_cameras",
    }
    for path in [
        component_root / "strings.json",
        component_root / "translations" / "ru.json",
        component_root / "translations" / "en.json",
    ]:
        data = json.loads(path.read_text())
        init = data["options"]["step"]["init"]
        assert "menu" not in init
        assert set(init["menu_options"]) == expected
        assert set(init["menu_option_descriptions"]) == expected
        assert all(init["menu_options"].values())
        assert all(init["menu_option_descriptions"].values())
