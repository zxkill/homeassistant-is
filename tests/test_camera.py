import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip(
    "voluptuous", reason="Модуль камеры использует voluptuous через пакет интеграции"
)

from custom_components.intersvyaz.camera import IntersvyazDoorCamera, async_setup_entry
from custom_components.intersvyaz.const import (
    CAMERA_FRAME_INTERVAL_SECONDS,
    DATA_DOOR_OPENERS,
    DOMAIN,
)


class _DummyResponse:
    def __init__(self, url: str, status: int = 200, payload: bytes | None = None) -> None:
        self._url = url
        self.status = status
        self._payload = payload or f"payload-for-{url}".encode()

    async def __aenter__(self) -> "_DummyResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def read(self) -> bytes:
        return self._payload


class _DummySession:
    def __init__(self) -> None:
        self.requested: List[str] = []
        self.responses: dict[str, _DummyResponse] = {}

    def get(self, url: str) -> _DummyResponse:
        self.requested.append(url)
        return self.responses.get(url, _DummyResponse(url))


@pytest.mark.asyncio
async def test_camera_setup_and_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Камера создаётся при наличии снимков и умеет обновлять изображение."""

    door_entry = {
        "uid": "entry_door_camera",
        "address": "Главный подъезд",
        "has_video": True,
        "image_url": "https://snapshots.example/initial.jpg",
    }
    hass = SimpleNamespace(data={DOMAIN: {"test-entry": {DATA_DOOR_OPENERS: [door_entry]}}})
    entry = SimpleNamespace(entry_id="test-entry")

    added_entities: List[IntersvyazDoorCamera] = []

    async def _add_entities(entities: List[IntersvyazDoorCamera]) -> None:
        added_entities.extend(entities)

    dummy_session = _DummySession()
    dummy_session.responses["https://snapshots.example/updated.jpg"] = _DummyResponse(
        "https://snapshots.example/updated.jpg", payload=b"new-bytes"
    )

    monkeypatch.setattr(
        "custom_components.intersvyaz.camera.async_get_clientsession",
        lambda _hass: dummy_session,
    )

    await async_setup_entry(hass, entry, _add_entities)
    assert len(added_entities) == 1, "Ожидаем одну камеру на домофон"

    camera = added_entities[0]
    assert camera.frame_interval == CAMERA_FRAME_INTERVAL_SECONDS

    # Обновляем ссылку в door_entry, имитируя работу обновления домофонов.
    door_entry["image_url"] = "https://snapshots.example/updated.jpg"
    image = await camera.async_camera_image()
    assert image == b"new-bytes"
    assert dummy_session.requested[-1] == "https://snapshots.example/updated.jpg"


@pytest.mark.asyncio
async def test_camera_skips_doors_without_video(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если домофон не поддерживает видео, камера не создаётся."""

    hass = SimpleNamespace(data={DOMAIN: {"entry": {DATA_DOOR_OPENERS: [
        {"uid": "door", "has_video": False, "image_url": "https://snapshots/door.jpg"}
    ]}}})
    entry = SimpleNamespace(entry_id="entry")
    captured: List[Any] = []

    async def _add_entities(entities: List[Any]) -> None:
        captured.extend(entities)

    monkeypatch.setattr(
        "custom_components.intersvyaz.camera.async_get_clientsession",
        lambda _hass: _DummySession(),
    )

    await async_setup_entry(hass, entry, _add_entities)
    assert not captured, "Камеры не должны создаваться для домофонов без снимков"
