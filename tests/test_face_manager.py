"""Тесты менеджера распознавания лиц Intersvyaz."""
from __future__ import annotations

import builtins
import importlib
import sys
import types
import warnings
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("voluptuous", reason="Зависимость интеграции требует voluptuous")

from custom_components.intersvyaz import face_manager
from custom_components.intersvyaz.const import (
    CONF_FACE_ENCODING,
    CONF_FACE_NAME,
    CONF_KNOWN_FACES,
    DATA_FACE_MANAGER,
    DOMAIN,
)
from custom_components.intersvyaz.face_manager import FaceRecognitionManager
from homeassistant.exceptions import HomeAssistantError


class _FakeFaceRecognition:
    """Минимальная подмена библиотеки face_recognition для тестов."""

    def __init__(self) -> None:
        self.loaded_images: List[bytes] = []
        self.encodings_queue: List[List[List[float]]] = []
        self.distances_queue: List[List[float]] = []

    def load_image_file(self, stream) -> bytes:
        data = stream.read()
        self.loaded_images.append(data)
        stream.seek(0)
        return data

    def face_encodings(self, _image) -> List[List[float]]:
        if self.encodings_queue:
            return self.encodings_queue.pop(0)
        return []

    def face_distance(self, _known, _encoding) -> List[float]:
        if self.distances_queue:
            return self.distances_queue.pop(0)
        return [1.0]


@pytest.mark.asyncio
async def test_face_manager_add_match_and_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    """Менеджер должен добавлять лица, распознавать их и удалять по запросу."""

    fake_module = _FakeFaceRecognition()
    monkeypatch.setattr(face_manager, "face_recognition", fake_module)

    async def _async_update_entry(entry_obj, *, data=None, options=None):
        if options is not None:
            entry_obj.options = options
        if data is not None:
            entry_obj.data = data

    hass = SimpleNamespace(
        data={DOMAIN: {"entry": {}}},
        config_entries=SimpleNamespace(
            async_update_entry=AsyncMock(side_effect=_async_update_entry)
        ),
    )

    async def _async_add_executor_job(func, *args):
        return func(*args)

    hass.async_add_executor_job = _async_add_executor_job

    entry = SimpleNamespace(entry_id="entry", options={})

    manager = FaceRecognitionManager(hass, entry, match_threshold=0.5, cooldown_seconds=60)

    fake_module.encodings_queue.append([[0.1, 0.2, 0.3]])

    await manager.async_add_known_face("Гость", b"sample-bytes")
    hass.config_entries.async_update_entry.assert_awaited_once()
    assert entry.options.get(CONF_KNOWN_FACES)
    assert hass.data[DOMAIN][entry.entry_id][DATA_FACE_MANAGER] is manager

    fake_module.encodings_queue.append([[0.1, 0.2, 0.3]])
    fake_module.distances_queue.append([0.4])

    open_callback = AsyncMock()
    await manager.async_process_image("door-uid", b"frame-bytes", open_callback)
    open_callback.assert_awaited_once()

    await manager.async_process_image("door-uid", b"frame-bytes", open_callback)
    assert open_callback.await_count == 1, "Повторный вызов должен быть заблокирован кулдауном"

    await manager.async_remove_known_face("Гость")
    assert not entry.options.get(CONF_KNOWN_FACES)
    assert hass.config_entries.async_update_entry.await_count == 2


@pytest.mark.asyncio
async def test_face_manager_requires_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """При отсутствии библиотеки распознавания менеджер сообщает об ошибке."""

    monkeypatch.setattr(face_manager, "face_recognition", None)

    async def _async_update_entry(entry_obj, *, data=None, options=None):
        if options is not None:
            entry_obj.options = options
        if data is not None:
            entry_obj.data = data

    hass = SimpleNamespace(
        data={DOMAIN: {"entry": {}}},
        config_entries=SimpleNamespace(
            async_update_entry=AsyncMock(side_effect=_async_update_entry)
        ),
    )

    async def _async_add_executor_job(func, *args):
        return func(*args)

    hass.async_add_executor_job = _async_add_executor_job

    entry = SimpleNamespace(entry_id="entry", options={})

    manager = FaceRecognitionManager(hass, entry)
    assert not manager.library_available

    with pytest.raises(HomeAssistantError):
        await manager.async_add_known_face("Кто-то", b"data")


def test_face_manager_lists_known_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Менеджер предоставляет копии списков лиц для UI и тестов."""

    monkeypatch.setattr(face_manager, "face_recognition", object())

    hass = SimpleNamespace(data={DOMAIN: {}})
    entry = SimpleNamespace(
        entry_id="entry",
        options={
            CONF_KNOWN_FACES: [
                {CONF_FACE_NAME: "Гость", CONF_FACE_ENCODING: [0.1, 0.2, 0.3]}
            ]
        },
    )

    manager = FaceRecognitionManager(hass, entry)

    names = manager.list_known_face_names()
    faces = manager.list_known_faces()

    assert names == ["Гость"]
    assert faces[0].name == "Гость"

    names.append("Друг")
    faces.append(face_manager.KnownFace(name="Друг", encoding=[0.4]))

    assert manager.list_known_face_names() == ["Гость"]
    assert len(manager.list_known_faces()) == 1


def test_face_manager_suppresses_pkg_resources_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При импорте модуля предупреждение о pkg_resources должно подавляться."""

    original_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        """Подмена импорта face_recognition, генерирующая предупреждение."""

        if name == "face_recognition" and level == 0:
            warnings.warn(
                "pkg_resources is deprecated as an API.",
                UserWarning,
            )
            module = types.ModuleType("face_recognition")
            module.face_encodings = lambda *_args, **_kwargs: []
            module.face_distance = lambda *_args, **_kwargs: []
            module.load_image_file = lambda stream: stream.read()
            sys.modules[name] = module
            return module
        return original_import(name, globals, locals, fromlist, level)

    # Убедимся, что предыдущие импорты не мешают воспроизведению предупреждения.
    monkeypatch.setitem(sys.modules, "face_recognition", None, raising=False)
    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        reloaded = importlib.reload(face_manager)

    # Предупреждение должно быть погашено локальным фильтром внутри face_manager.
    assert not caught
    assert reloaded._SUPPRESSED_PKG_RESOURCES_WARNING is True

    # Возвращаем исходное состояние, чтобы остальные тесты работали с чистым модулем.
    monkeypatch.undo()
    importlib.reload(face_manager)
