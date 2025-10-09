"""Тесты мастера настроек интеграции для управления лицами."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip(
    "voluptuous", reason="Options flow использует схемы voluptuous"
)

from custom_components.intersvyaz import config_flow
from custom_components.intersvyaz.const import (
    CONF_FACE_IMAGE,
    CONF_FACE_NAME,
    CONF_KNOWN_FACES,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_options_flow_init_menu_lists_faces() -> None:
    """Шаг меню должен показывать перечень лиц и доступные действия."""

    entry = SimpleNamespace(entry_id="entry", options={CONF_KNOWN_FACES: []})
    flow = config_flow.IntersvyazOptionsFlow(entry)
    flow.hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {}}})

    manager = SimpleNamespace(
        list_known_face_names=lambda: ["Гость"],
        library_available=True,
    )
    flow._async_resolve_face_manager = AsyncMock(return_value=manager)

    captured: dict[str, dict] = {}

    def _show_menu(**kwargs):
        captured["menu"] = kwargs
        return {"type": "menu"}

    flow.async_show_menu = _show_menu  # type: ignore[assignment]

    result = await flow.async_step_init()

    assert result["type"] == "menu"
    menu_args = captured["menu"]
    assert "add_face" in menu_args["menu_options"]
    assert "remove_face" in menu_args["menu_options"]
    assert "Гость" in menu_args["description_placeholders"]["known_faces"]


@pytest.mark.asyncio
async def test_options_flow_add_face_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Форма добавления должна сохранять лицо и завершаться успешно."""

    entry = SimpleNamespace(entry_id="entry", options={CONF_KNOWN_FACES: []})
    flow = config_flow.IntersvyazOptionsFlow(entry)
    flow.hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {}}})

    manager = SimpleNamespace(
        list_known_face_names=lambda: [],
        library_available=True,
        async_add_known_face=AsyncMock(),
    )
    flow._async_resolve_face_manager = AsyncMock(return_value=manager)

    captured: dict[str, dict] = {}

    def _show_form(**kwargs):
        captured["form"] = kwargs
        return {"type": "form"}

    flow.async_show_form = _show_form  # type: ignore[assignment]
    flow.async_create_entry = lambda **kwargs: {  # type: ignore[assignment]
        "type": "create_entry",
        **kwargs,
    }

    # Первый вызов без данных должен вернуть форму без ошибок.
    empty_result = await flow.async_step_add_face()
    assert empty_result["type"] == "form"
    assert captured["form"]["errors"] == {}

    upload = config_flow.UploadFile(b"image-bytes")
    user_input = {CONF_FACE_NAME: "Гость", CONF_FACE_IMAGE: upload}

    result = await flow.async_step_add_face(user_input)

    assert result["type"] == "create_entry"
    manager.async_add_known_face.assert_awaited_once_with("Гость", b"image-bytes")


@pytest.mark.asyncio
async def test_options_flow_add_face_requires_library() -> None:
    """При отсутствии библиотеки форма должна показать ошибку."""

    entry = SimpleNamespace(entry_id="entry", options={CONF_KNOWN_FACES: []})
    flow = config_flow.IntersvyazOptionsFlow(entry)
    flow.hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {}}})

    manager = SimpleNamespace(
        list_known_face_names=lambda: [],
        library_available=False,
        async_add_known_face=AsyncMock(),
    )
    flow._async_resolve_face_manager = AsyncMock(return_value=manager)

    captured: dict[str, dict] = {}

    def _show_form(**kwargs):
        captured["form"] = kwargs
        return {"type": "form"}

    flow.async_show_form = _show_form  # type: ignore[assignment]

    result = await flow.async_step_add_face()

    assert result["type"] == "form"
    assert captured["form"]["errors"]["base"] == "library_missing"


@pytest.mark.asyncio
async def test_options_flow_remove_face(monkeypatch: pytest.MonkeyPatch) -> None:
    """Удаление лица должно вызывать менеджер и закрывать мастер."""

    entry = SimpleNamespace(entry_id="entry", options={CONF_KNOWN_FACES: []})
    flow = config_flow.IntersvyazOptionsFlow(entry)
    flow.hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {}}})

    manager = SimpleNamespace(
        list_known_face_names=lambda: ["Гость"],
        async_remove_known_face=AsyncMock(),
    )
    flow._async_resolve_face_manager = AsyncMock(return_value=manager)

    flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}  # type: ignore[assignment]
    flow.async_create_entry = lambda **kwargs: {  # type: ignore[assignment]
        "type": "create_entry",
        **kwargs,
    }

    # Без входных данных возвращается форма с выбором лица.
    first_result = await flow.async_step_remove_face()
    assert first_result["type"] == "form"

    result = await flow.async_step_remove_face({CONF_FACE_NAME: "Гость"})

    assert result["type"] == "create_entry"
    manager.async_remove_known_face.assert_awaited_once_with("Гость")
