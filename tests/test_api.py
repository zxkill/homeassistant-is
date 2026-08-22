
def test_token_none_bug_is_not_present(component_root):
    source = (component_root / "api.py").read_text()
    assert 'str(payload.get("TOKEN"))' not in source
    assert "IntersvyazAuthError" in source
    assert "IntersvyazNetworkError" in source
    assert "safe_payload_summary" in source


def test_coordinator_data_does_not_expose_tokens(component_root):
    source = (component_root / "api.py").read_text()
    marker = "async def async_fetch_account_snapshot"
    block = source[source.index(marker):source.index("def set_mobile_token", source.index(marker))]
    assert '"mobile_token"' not in block
    assert '"crm_token"' not in block
