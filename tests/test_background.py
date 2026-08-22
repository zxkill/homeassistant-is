"""Тесты фоновой обработки Intersvyaz 2.0."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.intersvyaz import background
from custom_components.intersvyaz.const import (
    CONF_BACKGROUND_CAMERAS,
    DATA_DOOR_OPENERS,
    DATA_FACE_MANAGER,
    DATA_OPEN_DOOR,
    DOMAIN,
)


def test_default_background_prefers_main_camera() -> None:
    entry = SimpleNamespace(options={})
    doors = [
        {"uid": "shared", "has_video": True, "image_url": "x", "is_main": False},
        {"uid": "main", "has_video": True, "image_url": "y", "is_main": True},
    ]
    assert background.calculate_default_background_uids(entry, doors) == ["main"]


@pytest.mark.asyncio
async def test_background_uses_snapshot_manager(monkeypatch) -> None:
    door = {
        "uid": "door-1",
        "has_video": True,
        "image_url": "https://example/image",
        "is_main": True,
        "callback": AsyncMock(),
    }
    face_manager = SimpleNamespace(async_process_image=AsyncMock())
    entry = SimpleNamespace(
        entry_id="entry",
        options={CONF_BACKGROUND_CAMERAS: ["door-1"]},
    )
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry": {
                    DATA_DOOR_OPENERS: [door],
                    DATA_FACE_MANAGER: face_manager,
                    DATA_OPEN_DOOR: door["callback"],
                }
            }
        },
        async_create_task=lambda _coro: None,
    )

    snapshot_manager = SimpleNamespace(async_get_snapshot=AsyncMock(return_value=b"frame"))
    monkeypatch.setattr(
        background,
        "get_snapshot_manager",
        lambda _hass, _entry: snapshot_manager,
    )

    processor = background.DoorBackgroundProcessor(
        hass,
        entry,
        scheduler=lambda *_args, **_kwargs: (lambda: None),
    )
    await processor.async_setup()
    await processor.async_force_cycle()

    snapshot_manager.async_get_snapshot.assert_awaited_once_with(
        "door-1", "https://example/image"
    )
    face_manager.async_process_image.assert_awaited_once()
