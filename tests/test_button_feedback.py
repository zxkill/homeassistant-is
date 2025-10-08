"""Тесты пользовательской обратной связи кнопки открытия домофона."""
from __future__ import annotations

import asyncio
import enum
import sys
import types
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = PACKAGE_ROOT / "custom_components" / "intersvyaz"


@contextmanager
def _load_button_module() -> Iterator[types.ModuleType]:
    """Импортировать модуль кнопки с заглушками Home Assistant."""

    saved_modules: dict[str, types.ModuleType | None] = {}

    def set_module(name: str, module: types.ModuleType) -> None:
        """Сохранить оригинальный модуль и подменить его тестовой заглушкой."""

        if name not in saved_modules:
            saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = module

    # Подготавливаем заглушку для voluptuous, которую использует __init__ интеграции.
    voluptuous_module = types.ModuleType("voluptuous")

    class _Schema:  # pragma: no cover - минимальная реализация
        def __init__(self, schema: object) -> None:
            self._schema = schema

        def __call__(self, value: object) -> object:
            return value

    class _Marker(str):  # pragma: no cover - маркер Required/Optional
        pass

    def _required(key: str) -> str:
        return _Marker(key)

    def _optional(key: str) -> str:
        return _Marker(key)

    voluptuous_module.Schema = _Schema  # type: ignore[attr-defined]
    voluptuous_module.Required = _required  # type: ignore[attr-defined]
    voluptuous_module.Optional = _optional  # type: ignore[attr-defined]
    set_module("voluptuous", voluptuous_module)

    # Заглушка aiohttp, чтобы импорт клиента API не требовал реальной библиотеки.
    aiohttp_module = types.ModuleType("aiohttp")

    class _ClientError(Exception):  # pragma: no cover - базовая ошибка HTTP
        pass

    class _ClientResponse:  # pragma: no cover - плейсхолдер ответа
        pass

    class _ClientSession:  # pragma: no cover - плейсхолдер сессии
        async def close(self) -> None:
            return None

    aiohttp_module.ClientError = _ClientError  # type: ignore[attr-defined]
    aiohttp_module.ClientResponse = _ClientResponse  # type: ignore[attr-defined]
    aiohttp_module.ClientSession = _ClientSession  # type: ignore[attr-defined]
    set_module("aiohttp", aiohttp_module)

    # Готовим пакет homeassistant и минимальные подмодули, чтобы импорт прошёл без зависимостей.
    ha_module = types.ModuleType("homeassistant")
    set_module("homeassistant", ha_module)

    const_module = types.ModuleType("homeassistant.const")

    class _Platform(enum.Enum):  # pragma: no cover - минимум значений платформ
        BUTTON = "button"
        SENSOR = "sensor"

    const_module.Platform = _Platform  # type: ignore[attr-defined]
    set_module("homeassistant.const", const_module)
    ha_module.const = const_module  # type: ignore[attr-defined]

    components_module = types.ModuleType("homeassistant.components")
    button_module = types.ModuleType("homeassistant.components.button")

    class _ButtonEntity:  # pragma: no cover - базовый класс-заглушка
        """Минимальная реализация ButtonEntity для тестов."""

        def __init__(self) -> None:
            self._state_write_count = 0
            self.hass = None
            self._attr_available = True
            self._attr_extra_state_attributes: dict[str, object] | None = None

        def async_write_ha_state(self) -> None:
            """Фиксируем факт обновления состояния."""

            self._state_write_count += 1

        @property
        def available(self) -> bool:
            """Вернуть признак доступности кнопки."""

            return getattr(self, "_attr_available", True)

        @property
        def extra_state_attributes(self) -> dict[str, object] | None:
            """Вернуть словарь атрибутов, если он заполнен тестируемым кодом."""

            return getattr(self, "_attr_extra_state_attributes", None)

    button_module.ButtonEntity = _ButtonEntity  # type: ignore[attr-defined]
    components_module.button = button_module  # type: ignore[attr-defined]
    set_module("homeassistant.components", components_module)
    set_module("homeassistant.components.button", button_module)

    config_entries_module = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:  # pragma: no cover - простая структура для entry
        entry_id = "test-entry"

    config_entries_module.ConfigEntry = _ConfigEntry  # type: ignore[attr-defined]
    set_module("homeassistant.config_entries", config_entries_module)

    core_module = types.ModuleType("homeassistant.core")

    class _HomeAssistant:  # pragma: no cover - минимальный контейнер
        pass

    core_module.HomeAssistant = _HomeAssistant  # type: ignore[attr-defined]
    
    class _ServiceCall:  # pragma: no cover - структура сервиса
        def __init__(self, data: dict[str, object] | None = None) -> None:
            self.data = data or {}

    core_module.ServiceCall = _ServiceCall  # type: ignore[attr-defined]
    set_module("homeassistant.core", core_module)

    helpers_module = types.ModuleType("homeassistant.helpers")

    device_registry_module = types.ModuleType("homeassistant.helpers.device_registry")

    class _DeviceInfo:  # pragma: no cover - контейнер для идентификаторов
        def __init__(self, **kwargs: object) -> None:
            self.data = kwargs

    device_registry_module.DeviceInfo = _DeviceInfo  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.device_registry", device_registry_module)

    entity_platform_module = types.ModuleType("homeassistant.helpers.entity_platform")

    def _add_entities_callback(*_args, **_kwargs) -> None:  # pragma: no cover - заглушка
        return None

    entity_platform_module.AddEntitiesCallback = _add_entities_callback  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.entity_platform", entity_platform_module)

    update_coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _CoordinatorEntity(_ButtonEntity):  # pragma: no cover - сохраняет координатор
        def __init__(self, coordinator: object) -> None:
            super().__init__()
            self.coordinator = coordinator

    class _DataUpdateCoordinator:  # pragma: no cover - заглушка координатора
        def __init__(self, hass: object, logger: object, name: str, update_interval: object) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval

        async def async_config_entry_first_refresh(self) -> None:
            return None

        @classmethod
        def __class_getitem__(cls, _item: object) -> type:
            return cls

    class _UpdateFailed(Exception):  # pragma: no cover - маркер ошибки обновления
        pass

    update_coordinator_module.CoordinatorEntity = _CoordinatorEntity  # type: ignore[attr-defined]
    update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator  # type: ignore[attr-defined]
    update_coordinator_module.UpdateFailed = _UpdateFailed  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.update_coordinator", update_coordinator_module)

    config_validation_module = types.ModuleType("homeassistant.helpers.config_validation")

    def _cv_string(value: object) -> object:  # pragma: no cover - эмуляция cv.string
        return value

    config_validation_module.string = _cv_string  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.config_validation", config_validation_module)

    aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")

    async def _async_get_clientsession(_hass: object) -> object:  # pragma: no cover
        return object()

    aiohttp_client_module.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.aiohttp_client", aiohttp_client_module)

    helpers_module.device_registry = device_registry_module  # type: ignore[attr-defined]
    helpers_module.entity_platform = entity_platform_module  # type: ignore[attr-defined]
    helpers_module.update_coordinator = update_coordinator_module  # type: ignore[attr-defined]
    helpers_module.config_validation = config_validation_module  # type: ignore[attr-defined]
    helpers_module.aiohttp_client = aiohttp_client_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers", helpers_module)

    exceptions_module = types.ModuleType("homeassistant.exceptions")

    class _HomeAssistantError(Exception):  # pragma: no cover - базовая ошибка
        pass

    exceptions_module.HomeAssistantError = _HomeAssistantError  # type: ignore[attr-defined]
    set_module("homeassistant.exceptions", exceptions_module)

    # Гарантируем наличие namespace-пакета custom_components.
    custom_components_pkg = sys.modules.get("custom_components")
    if not custom_components_pkg:
        custom_components_pkg = types.ModuleType("custom_components")
        custom_components_pkg.__path__ = [str(PACKAGE_ROOT / "custom_components")]
        sys.modules["custom_components"] = custom_components_pkg

    try:
        const_spec = spec_from_file_location(
            "custom_components.intersvyaz.const",
            INTEGRATION_ROOT / "const.py",
        )
        assert const_spec and const_spec.loader
        const_module = module_from_spec(const_spec)
        set_module("custom_components.intersvyaz.const", const_module)
        const_spec.loader.exec_module(const_module)  # type: ignore[union-attr]

        spec = spec_from_file_location(
            "custom_components.intersvyaz.button",
            INTEGRATION_ROOT / "button.py",
        )
        assert spec and spec.loader
        module = module_from_spec(spec)
        sys.modules["custom_components.intersvyaz.button"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        yield module
    finally:
        sys.modules.pop("custom_components.intersvyaz.button", None)
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_button_shows_success_and_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Статус «Открыто» отображается несколько секунд и затем сбрасывается."""

    async def _scenario(module: types.ModuleType) -> None:
        monkeypatch.setattr(module, "BUTTON_STATUS_RESET_DELAY_SECONDS", 0)
        open_mock = AsyncMock()
        button = module.IntersvyazDoorOpenButton(
            coordinator=SimpleNamespace(),
            entry=SimpleNamespace(entry_id="entry-test"),
            open_door_callable=open_mock,
            door_entry={"uid": "door-1", "address": "Подъезд 1"},
        )
        button.hass = SimpleNamespace(loop=asyncio.get_running_loop())
        base_name = button.name

        assert button.state == module.STATUS_READY
        assert button.available is True

        await button.async_press()

        assert open_mock.await_count == 1
        assert button.name.endswith(module.STATUS_OPENED)
        assert button.state == module.STATUS_OPENED
        assert button.available is False
        assert button.extra_state_attributes == {
            "door_uid": "door-1",
            "door_address": "Подъезд 1",
            "door_mac": None,
            "door_id": None,
            "status": module.STATUS_OPENED,
            "busy": True,
        }

        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert button.name == base_name
        assert button._is_busy is False
        assert button.state == module.STATUS_READY
        assert button.available is True
        assert button.extra_state_attributes == {
            "door_uid": "door-1",
            "door_address": "Подъезд 1",
            "door_mac": None,
            "door_id": None,
            "status": module.STATUS_READY,
            "busy": False,
        }
        assert getattr(button, "_state_write_count", 0) >= 3

    with _load_button_module() as module:
        asyncio.run(_scenario(module))


def test_button_displays_error_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """При ошибке отображается статус «Ошибка», который позже сбрасывается."""

    async def _scenario(module: types.ModuleType) -> None:
        monkeypatch.setattr(module, "BUTTON_STATUS_RESET_DELAY_SECONDS", 0)
        error = module.IntersvyazApiError("boom")
        open_mock = AsyncMock(side_effect=error)
        button = module.IntersvyazDoorOpenButton(
            coordinator=SimpleNamespace(),
            entry=SimpleNamespace(entry_id="entry-test"),
            open_door_callable=open_mock,
            door_entry={"uid": "door-1", "address": "Подъезд 1"},
        )
        button.hass = SimpleNamespace(loop=asyncio.get_running_loop())
        base_name = button.name

        with pytest.raises(module.IntersvyazApiError):
            await button.async_press()

        assert button.name.endswith(module.STATUS_ERROR)
        assert button.state == module.STATUS_ERROR
        assert button.available is False

        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert button.name == base_name
        assert button._is_busy is False
        assert button.state == module.STATUS_READY
        assert button.available is True
        assert getattr(button, "_state_write_count", 0) >= 3

    with _load_button_module() as module:
        asyncio.run(_scenario(module))


def test_button_ignores_clicks_while_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторные клики, пока статус не сброшен, игнорируются."""

    async def _scenario(module: types.ModuleType) -> None:
        monkeypatch.setattr(module, "BUTTON_STATUS_RESET_DELAY_SECONDS", 0)
        open_mock = AsyncMock()
        button = module.IntersvyazDoorOpenButton(
            coordinator=SimpleNamespace(),
            entry=SimpleNamespace(entry_id="entry-test"),
            open_door_callable=open_mock,
            door_entry={"uid": "door-1", "address": "Подъезд 1"},
        )
        button.hass = SimpleNamespace(loop=asyncio.get_running_loop())

        await button.async_press()
        assert open_mock.await_count == 1

        # Повторный клик выполняем до сброса статуса.
        await button.async_press()
        assert open_mock.await_count == 1

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert button._is_busy is False
        assert button.state == module.STATUS_READY

    with _load_button_module() as module:
        asyncio.run(_scenario(module))
