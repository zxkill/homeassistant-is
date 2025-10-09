from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List
import sys
import types

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if "aiohttp" not in sys.modules:
    aiohttp_stub = types.ModuleType("aiohttp")

    class _ClientError(Exception):
        pass

    aiohttp_stub.ClientError = _ClientError  # type: ignore[attr-defined]
    sys.modules["aiohttp"] = aiohttp_stub

from custom_components.intersvyaz.background import DoorBackgroundProcessor
from custom_components.intersvyaz.const import (
    CONF_BACKGROUND_CAMERAS,
    DATA_DOOR_OPENERS,
    DATA_FACE_MANAGER,
    DATA_OPEN_DOOR,
    DOMAIN,
)


class _DummyResponse:
    """Асинхронный ответ HTTP-клиента для эмуляции снимка домофона."""

    def __init__(self, url: str, payload: bytes | None = None) -> None:
        self.status = 200
        self._payload = payload or f"payload-{url}".encode()

    async def __aenter__(self) -> "_DummyResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def read(self) -> bytes:
        return self._payload


class _DummySession:
    """Упрощённый aiohttp-сессион для отслеживания запросов фонового менеджера."""

    def __init__(self) -> None:
        self.requested: List[str] = []
        self.payloads: dict[str, _DummyResponse] = {}

    def get(self, url: str) -> _DummyResponse:
        self.requested.append(url)
        return self.payloads.get(url, _DummyResponse(url))


class _DummyFaceManager:
    """Заглушка менеджера распознавания, сохраняющая входные данные."""

    def __init__(self) -> None:
        self.calls: List[tuple[str, bytes, Callable[[], Any] | None]] = []

    async def async_process_image(
        self, door_uid: str, image: bytes, callback: Callable[[], Any] | None
    ) -> None:
        self.calls.append((door_uid, image, callback))


@pytest.mark.asyncio
async def test_background_processor_fetches_selected_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """Фоновый менеджер должен загружать кадры и передавать их в распознавание."""

    door_entry = {
        "uid": "door-1",
        "has_video": True,
        "image_url": "https://snapshots.example/door-1.jpg",
        "address": "Главный подъезд",
        "callback": lambda: None,
        "is_main": True,
    }

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry": {
                    DATA_DOOR_OPENERS: [door_entry],
                    DATA_FACE_MANAGER: _DummyFaceManager(),
                    DATA_OPEN_DOOR: lambda: None,
                }
            }
        }
    )
    entry = SimpleNamespace(entry_id="entry", options={CONF_BACKGROUND_CAMERAS: ["door-1"]})

    dummy_session = _DummySession()
    dummy_session.payloads["https://snapshots.example/door-1.jpg"] = _DummyResponse(
        "https://snapshots.example/door-1.jpg", payload=b"door-one"
    )

    monkeypatch.setattr(
        "custom_components.intersvyaz.background.async_get_clientsession",
        lambda _hass: dummy_session,
    )

    scheduled: dict[str, Any] = {}

    def _fake_scheduler(_hass, action, interval: timedelta):
        scheduled["interval"] = interval
        scheduled["action"] = action
        return lambda: scheduled.setdefault("cancelled", True)

    processor = DoorBackgroundProcessor(
        hass,
        entry,
        interval_seconds=2,
        scheduler=_fake_scheduler,
    )

    await processor.async_setup()
    assert processor.selected_uids == {"door-1"}
    assert isinstance(scheduled.get("interval"), timedelta)

    await processor.async_force_cycle()

    assert dummy_session.requested == ["https://snapshots.example/door-1.jpg"]
    manager = hass.data[DOMAIN]["entry"][DATA_FACE_MANAGER]
    assert manager.calls
    assert manager.calls[-1][0] == "door-1"
    assert manager.calls[-1][1] == b"door-one"


@pytest.mark.asyncio
async def test_background_processor_uses_default_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если пользователь не выбрал камеры, используется основной домофон."""

    door_entry = {
        "uid": "door-main",
        "has_video": True,
        "image_url": "https://snapshots.example/default.jpg",
        "address": "Основной подъезд",
        "is_main": True,
    }

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry": {
                    DATA_DOOR_OPENERS: [door_entry],
                    DATA_FACE_MANAGER: _DummyFaceManager(),
                    DATA_OPEN_DOOR: lambda: None,
                }
            }
        }
    )
    entry = SimpleNamespace(entry_id="entry", options={})

    monkeypatch.setattr(
        "custom_components.intersvyaz.background.async_get_clientsession",
        lambda _hass: _DummySession(),
    )

    processor = DoorBackgroundProcessor(hass, entry, interval_seconds=3)
    await processor.async_setup()

    assert processor.selected_uids == {"door-main"}
