"""Тесты общего кеша снимков домофонов."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.intersvyaz import snapshot


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def read(self):
        return b"jpeg-frame"


class _Session:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _url):
        self.calls += 1
        return _Response()


@pytest.mark.asyncio
async def test_snapshot_manager_reuses_fresh_frame(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(snapshot, "async_get_clientsession", lambda _hass: session)
    manager = snapshot.DoorSnapshotManager(SimpleNamespace(), cache_ttl=10)

    first = await manager.async_get_snapshot("door", "https://example/image")
    second = await manager.async_get_snapshot("door", "https://example/image")

    assert first == b"jpeg-frame"
    assert second == first
    assert session.calls == 1
