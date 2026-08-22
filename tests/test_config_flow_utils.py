
def test_reauth_and_reconfigure_are_present(component_root):
    source = (component_root / "config_flow.py").read_text()
    assert "async_step_reauth" in source
    assert "async_step_reauth_confirm" in source
    assert "async_step_reconfigure" in source
    assert "_abort_if_unique_id_mismatch" in source
    assert "async_update_reload_and_abort" in source


def test_config_flow_does_not_log_raw_final_data(component_root):
    source = (component_root / "config_flow.py").read_text()
    assert "Создаём конфигурацию с данными" not in source
