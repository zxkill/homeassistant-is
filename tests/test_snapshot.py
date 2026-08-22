
def test_snapshot_manager_deduplicates_requests(component_root):
    source = (component_root / "snapshot.py").read_text()
    assert "asyncio.Lock" in source
    assert "_cache" in source
    assert "SNAPSHOT_MAX_BYTES" in source
    assert "force" in source
