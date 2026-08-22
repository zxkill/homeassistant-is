"""Тесты камеры Intersvyaz 2.0."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.intersvyaz import camera
from custom_components.intersvyaz.const import DATA_DOOR_OPENERS, DATA_FACE_MANAGER, DOMAIN


@pytest.mark.asyncio
async def test_camera_uses_shared_snapshot_manager(monkeypatch) -> None:
    entry = SimpleNamespace(entry_id="entry")
    face_manager = SimpleNamespace(async_process_image=AsyncMock())
    door = {
        "uid": "door-1",
        "address": "Подъезд 1",
        "has_video": True,
        "image_url": "https://example/image",
        "callback": AsyncMock(),
    }
    hass = SimpleNamespace(
        data={DOMAIN: {"entry": {DATA_DOOR_OPENERS: [door], DATA_FACE_MANAGER: face_manager}}}
    )
    snapshot_manager = SimpleNamespace(async_get_snapshot=AsyncMock(return_value=b"frame"))
    monkeypatch.setattr(camera, "get_snapshot_manager", lambda _hass, _entry: snapshot_manager)

    entity = camera.IntersvyazDoorCamera(hass, entry, door)
    result = await entity.async_camera_image()

    assert result == b"frame"
    snapshot_manager.async_get_snapshot.assert_awaited_once_with(
        "door-1", "https://example/image"
    )
    face_manager.async_process_image.assert_awaited_once()
