
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


def test_config_flow_has_no_undefined_uppercase_globals(component_root):
    """Все константы, используемые config flow, должны быть импортированы/определены."""

    import ast
    import re

    source = (component_root / "config_flow.py").read_text()
    tree = ast.parse(source)

    imported: set[str] = set()
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defined.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )

    referenced = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and re.fullmatch(r"[A-Z][A-Z0-9_]+", node.id)
    }
    missing = referenced - imported - defined

    assert not missing, f"Не импортированы/не определены константы: {sorted(missing)}"
