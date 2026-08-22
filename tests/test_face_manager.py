"""Тесты нового локального менеджера распознавания Intersvyaz 2.0."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.intersvyaz.const import (
    CONF_FACE_ENCODING,
    CONF_FACE_NAME,
    CONF_KNOWN_FACES,
    EVENT_FACE_RECOGNIZED,
    EVENT_UNKNOWN_PERSON,
)
from custom_components.intersvyaz.face_manager import FaceRecognitionManager
from custom_components.intersvyaz.recognition import FaceRecognitionResult
from homeassistant.exceptions import HomeAssistantError


class _FakeEngine:
    available = True

    def __init__(self) -> None:
        self.encoding = [0.1, 0.2, 0.3]
        self.result = FaceRecognitionResult(faces_detected=0)
        self.recognize_calls = 0

    def extract_single_encoding(self, _image: bytes):
        return list(self.encoding)

    def recognize(self, _image, _known, _threshold):
        self.recognize_calls += 1
        return self.result


class _ConfigEntries:
    def async_update_entry(self, entry, *, data=None, options=None):
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, dict(data)))


def _make_hass():
    hass = SimpleNamespace(data={}, config_entries=_ConfigEntries(), bus=_Bus())

    async def _async_add_executor_job(func, *args):
        return func(*args)

    hass.async_add_executor_job = _async_add_executor_job
    return hass


@pytest.mark.asyncio
async def test_add_recognize_event_and_open() -> None:
    hass = _make_hass()
    entry = SimpleNamespace(entry_id="entry", options={}, data={})
    engine = _FakeEngine()
    manager = FaceRecognitionManager(hass, entry, engine=engine, event_cooldown_seconds=0)

    await manager.async_add_known_face("Алексей", b"portrait")
    stored = entry.options[CONF_KNOWN_FACES][0]
    assert stored[CONF_FACE_NAME] == "Алексей"
    assert stored[CONF_FACE_ENCODING] == engine.encoding

    engine.result = FaceRecognitionResult(
        faces_detected=1,
        matched_name="Алексей",
        distance=0.41,
    )
    opener = AsyncMock()
    await manager.async_process_image("door-1", b"frame-1", opener)

    opener.assert_awaited_once()
    assert hass.bus.events[0][0] == EVENT_FACE_RECOGNIZED
    assert hass.bus.events[0][1]["person"] == "Алексей"
    assert hass.bus.events[0][1]["distance"] == 0.41


@pytest.mark.asyncio
async def test_unknown_person_fires_event_without_open() -> None:
    hass = _make_hass()
    entry = SimpleNamespace(
        entry_id="entry",
        options={
            CONF_KNOWN_FACES: [
                {CONF_FACE_NAME: "Алексей", CONF_FACE_ENCODING: [0.1, 0.2, 0.3]}
            ]
        },
        data={},
    )
    engine = _FakeEngine()
    engine.result = FaceRecognitionResult(faces_detected=1, distance=0.81)
    manager = FaceRecognitionManager(hass, entry, engine=engine, event_cooldown_seconds=0)
    opener = AsyncMock()

    await manager.async_process_image("door-1", b"unknown", opener)

    opener.assert_not_awaited()
    assert hass.bus.events[0][0] == EVENT_UNKNOWN_PERSON
    assert hass.bus.events[0][1]["faces_detected"] == 1


@pytest.mark.asyncio
async def test_identical_frame_is_not_processed_twice() -> None:
    hass = _make_hass()
    entry = SimpleNamespace(
        entry_id="entry",
        options={
            CONF_KNOWN_FACES: [
                {CONF_FACE_NAME: "Алексей", CONF_FACE_ENCODING: [0.1, 0.2, 0.3]}
            ]
        },
        data={},
    )
    engine = _FakeEngine()
    engine.result = FaceRecognitionResult(faces_detected=0)
    manager = FaceRecognitionManager(hass, entry, engine=engine)

    await manager.async_process_image("door-1", b"same-frame", None)
    await manager.async_process_image("door-1", b"same-frame", None)

    assert engine.recognize_calls == 1


@pytest.mark.asyncio
async def test_remove_face() -> None:
    hass = _make_hass()
    entry = SimpleNamespace(
        entry_id="entry",
        options={
            CONF_KNOWN_FACES: [
                {CONF_FACE_NAME: "Гость", CONF_FACE_ENCODING: [0.1, 0.2, 0.3]}
            ]
        },
        data={},
    )
    manager = FaceRecognitionManager(hass, entry, engine=_FakeEngine())

    await manager.async_remove_known_face("Гость")
    assert entry.options[CONF_KNOWN_FACES] == []

    with pytest.raises(HomeAssistantError):
        await manager.async_remove_known_face("Гость")
