"""Проверяем генерацию событий открытия домофона."""
from __future__ import annotations

import enum
import sys
import types
import asyncio
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = PACKAGE_ROOT / "custom_components" / "intersvyaz"


@contextmanager
def _load_init_module() -> types.ModuleType:
    """Импортировать модуль интеграции с заглушками Home Assistant."""

    saved_modules: dict[str, types.ModuleType | None] = {}

    def set_module(name: str, module: types.ModuleType) -> None:
        """Сохранить оригинал и зарегистрировать заглушку."""

        if name not in saved_modules:
            saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = module

    # Заглушка для voluptuous.Schema
    voluptuous_module = types.ModuleType("voluptuous")

    class _Schema:  # pragma: no cover - минимальный аналог
        def __init__(self, schema: object) -> None:
            self._schema = schema

        def __call__(self, value: object) -> object:
            return value

    class _Marker(str):  # pragma: no cover - плейсхолдер ключа схемы
        pass

    def _required(key: str) -> str:
        return _Marker(key)

    def _optional(key: str) -> str:
        return _Marker(key)

    voluptuous_module.Schema = _Schema  # type: ignore[attr-defined]
    voluptuous_module.Required = _required  # type: ignore[attr-defined]
    voluptuous_module.Optional = _optional  # type: ignore[attr-defined]
    set_module("voluptuous", voluptuous_module)

    # Базовые пакеты Home Assistant
    ha_module = types.ModuleType("homeassistant")
    set_module("homeassistant", ha_module)

    const_module = types.ModuleType("homeassistant.const")

    class _Platform(enum.Enum):  # pragma: no cover - достаточно перечисления
        SENSOR = "sensor"
        BUTTON = "button"

    const_module.Platform = _Platform  # type: ignore[attr-defined]
    ha_module.const = const_module  # type: ignore[attr-defined]
    set_module("homeassistant.const", const_module)

    config_entries_module = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:  # pragma: no cover - пустой контейнер
        entry_id = "test"

    config_entries_module.ConfigEntry = _ConfigEntry  # type: ignore[attr-defined]
    set_module("homeassistant.config_entries", config_entries_module)

    core_module = types.ModuleType("homeassistant.core")

    class _HomeAssistant:  # pragma: no cover - минимальный контейнер
        pass

    class _ServiceCall:  # pragma: no cover - структура вызова сервиса
        def __init__(self, data: dict[str, Any] | None = None) -> None:
            self.data = data or {}

    core_module.HomeAssistant = _HomeAssistant  # type: ignore[attr-defined]
    core_module.ServiceCall = _ServiceCall  # type: ignore[attr-defined]
    set_module("homeassistant.core", core_module)

    exceptions_module = types.ModuleType("homeassistant.exceptions")

    class _HomeAssistantError(Exception):  # pragma: no cover
        pass

    exceptions_module.HomeAssistantError = _HomeAssistantError  # type: ignore[attr-defined]
    set_module("homeassistant.exceptions", exceptions_module)

    helpers_module = types.ModuleType("homeassistant.helpers")

    config_validation_module = types.ModuleType("homeassistant.helpers.config_validation")

    def _cv_string(value: object) -> object:  # pragma: no cover - имитация cv.string
        return value

    config_validation_module.string = _cv_string  # type: ignore[attr-defined]
    helpers_module.config_validation = config_validation_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.config_validation", config_validation_module)

    aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")

    async def _async_get_clientsession(_hass: object) -> object:  # pragma: no cover
        return object()

    aiohttp_client_module.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
    helpers_module.aiohttp_client = aiohttp_client_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.aiohttp_client", aiohttp_client_module)

    update_coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _DataUpdateCoordinator:  # pragma: no cover - урезанный аналог
        def __init__(self, hass: object, logger: object, name: str, update_interval: object) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval

    class _UpdateFailed(Exception):  # pragma: no cover - маркер исключения
        pass

    update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator  # type: ignore[attr-defined]
    update_coordinator_module.UpdateFailed = _UpdateFailed  # type: ignore[attr-defined]
    helpers_module.update_coordinator = update_coordinator_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.update_coordinator", update_coordinator_module)

    set_module("homeassistant.helpers", helpers_module)

    # Гарантируем существование namespace-пакета custom_components
    custom_components_pkg = sys.modules.get("custom_components")
    if not custom_components_pkg:
        custom_components_pkg = types.ModuleType("custom_components")
        custom_components_pkg.__path__ = [str(PACKAGE_ROOT / "custom_components")]
        sys.modules["custom_components"] = custom_components_pkg

    # Загружаем сам модуль интеграции
    spec = spec_from_file_location(
        "custom_components.intersvyaz.__init__",
        INTEGRATION_ROOT / "__init__.py",
    )
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules["custom_components.intersvyaz"] = module  # type: ignore[assignment]
    sys.modules["custom_components.intersvyaz.__init__"] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        yield module
    finally:
        sys.modules.pop("custom_components.intersvyaz.__init__", None)
        sys.modules.pop("custom_components.intersvyaz", None)
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class _FakeBus:
    """Минимальная реализация шины событий Home Assistant."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Сохраняем вызванное событие для проверки."""

        self.events.append((event_type, event_data))


def test_build_open_door_callable_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Событие об успешном открытии содержит базовый набор полей."""

    with _load_init_module() as init_module:
        from custom_components.intersvyaz.api import IntersvyazApiError  # noqa: F401
        from custom_components.intersvyaz.const import DOMAIN, EVENT_DOOR_OPEN_RESULT

        hass = SimpleNamespace(bus=_FakeBus())
        entry = SimpleNamespace(entry_id="entry-test")
        api_client = SimpleNamespace(async_open_door=AsyncMock())
        persist_mock = AsyncMock()
        monkeypatch.setattr(init_module, "_persist_tokens", persist_mock)

        open_callable = init_module.build_open_door_callable(
            hass,
            entry,
            api_client,
            mac="AA:BB:CC:DD:EE:FF",
            door_id=2,
            address="ул. Гагарина, 5",
            door_uid="entry-test_door",
        )

        asyncio.run(open_callable())

        api_client.async_open_door.assert_awaited_once_with("AA:BB:CC:DD:EE:FF", 2)
        persist_mock.assert_awaited_once()
        assert hass.bus.events == [
            (
                f"{DOMAIN}_{EVENT_DOOR_OPEN_RESULT}",
                {
                    "entry_id": "entry-test",
                    "door_uid": "entry-test_door",
                    "address": "ул. Гагарина, 5",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "door_id": 2,
                    "success": True,
                },
            )
        ]


def test_build_open_door_callable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Неудачное открытие публикует событие с описанием ошибки."""

    with _load_init_module() as init_module:
        from custom_components.intersvyaz.api import IntersvyazApiError
        from custom_components.intersvyaz.const import DOMAIN, EVENT_DOOR_OPEN_RESULT

        hass = SimpleNamespace(bus=_FakeBus())
        entry = SimpleNamespace(entry_id="entry-fail")
        api_client = SimpleNamespace(
            async_open_door=AsyncMock(side_effect=IntersvyazApiError("boom"))
        )
        persist_mock = AsyncMock()
        monkeypatch.setattr(init_module, "_persist_tokens", persist_mock)

        open_callable = init_module.build_open_door_callable(
            hass,
            entry,
            api_client,
            mac="11:22:33:44:55:66",
            door_id=3,
            address="ул. Пушкина, 10",
            door_uid="entry-fail_door",
        )

        with pytest.raises(IntersvyazApiError):
            asyncio.run(open_callable())

        persist_mock.assert_not_called()
        assert hass.bus.events == [
            (
                f"{DOMAIN}_{EVENT_DOOR_OPEN_RESULT}",
                {
                    "entry_id": "entry-fail",
                    "door_uid": "entry-fail_door",
                    "address": "ул. Пушкина, 10",
                    "mac": "11:22:33:44:55:66",
                    "door_id": 3,
                    "success": False,
                    "error": "boom",
                },
            )
        ]
