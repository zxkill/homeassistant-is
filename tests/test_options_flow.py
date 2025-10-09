"""Тесты options flow для интеграции Intersvyaz."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Any, Dict, Iterator

from unittest.mock import AsyncMock

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz"


@contextmanager
def _load_config_flow_module(*, library_available: bool) -> Iterator[types.ModuleType]:
    """Загрузить модуль config_flow с заглушками Home Assistant."""

    saved_modules: Dict[str, types.ModuleType | None] = {}

    def set_module(name: str, module: types.ModuleType) -> None:
        """Подменить модуль и запомнить оригинал для последующего восстановления."""

        if name not in saved_modules:
            saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = module

    try:
        # --- Заглушки пакета homeassistant ---
        ha_module = types.ModuleType("homeassistant")
        set_module("homeassistant", ha_module)

        config_entries_module = types.ModuleType("homeassistant.config_entries")

        class _ConfigFlow:  # pragma: no cover - базовая заглушка
            def __init_subclass__(cls, *, domain: str | None = None, **kwargs: Any) -> None:
                super().__init_subclass__(**kwargs)
                cls.DOMAIN = domain

            async def async_set_unique_id(self, *_: Any, **__: Any) -> None:
                return None

        class _OptionsFlow:
            def async_show_menu(
                self,
                *,
                step_id: str,
                menu_options: Dict[str, str],
                description_placeholders: Dict[str, str] | None = None,
            ) -> Dict[str, Any]:
                return {
                    "type": "menu",
                    "step_id": step_id,
                    "menu_options": menu_options,
                    "description_placeholders": description_placeholders or {},
                }

            def async_show_form(
                self,
                *,
                step_id: str,
                data_schema: Any,
                errors: Dict[str, str] | None = None,
                description_placeholders: Dict[str, str] | None = None,
            ) -> Dict[str, Any]:
                return {
                    "type": "form",
                    "step_id": step_id,
                    "data_schema": data_schema,
                    "errors": errors or {},
                    "description_placeholders": description_placeholders or {},
                }

            def async_create_entry(
                self, *, title: str, data: Dict[str, Any]
            ) -> Dict[str, Any]:
                return {"type": "create_entry", "title": title, "data": data}

        class _ConfigEntry:  # pragma: no cover - используется только как тип
            pass

        config_entries_module.ConfigFlow = _ConfigFlow  # type: ignore[attr-defined]
        config_entries_module.OptionsFlow = _OptionsFlow  # type: ignore[attr-defined]
        config_entries_module.ConfigEntry = _ConfigEntry  # type: ignore[attr-defined]
        set_module("homeassistant.config_entries", config_entries_module)
        ha_module.config_entries = config_entries_module  # type: ignore[attr-defined]

        data_entry_flow_module = types.ModuleType("homeassistant.data_entry_flow")

        class _UploadFile:  # pragma: no cover - в тестах не используется
            async def async_read(self) -> bytes:
                return b""

        data_entry_flow_module.FlowResult = Dict[str, Any]  # type: ignore[attr-defined]
        data_entry_flow_module.UploadFile = _UploadFile  # type: ignore[attr-defined]
        set_module("homeassistant.data_entry_flow", data_entry_flow_module)
        ha_module.data_entry_flow = data_entry_flow_module  # type: ignore[attr-defined]

        core_module = types.ModuleType("homeassistant.core")

        def callback(func):  # pragma: no cover - простой декоратор-заглушка
            return func

        class _HomeAssistant:  # pragma: no cover - минимальная реализация
            def __init__(self) -> None:
                self.data: Dict[str, Any] = {}
                self.config_entries = types.SimpleNamespace(
                    async_update_entry=AsyncMock()
                )

            async def async_add_executor_job(self, func, *args):
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, func, *args)

        core_module.callback = callback  # type: ignore[attr-defined]
        core_module.HomeAssistant = _HomeAssistant  # type: ignore[attr-defined]
        set_module("homeassistant.core", core_module)
        ha_module.core = core_module  # type: ignore[attr-defined]

        exceptions_module = types.ModuleType("homeassistant.exceptions")

        class _HomeAssistantError(Exception):
            pass

        exceptions_module.HomeAssistantError = _HomeAssistantError  # type: ignore[attr-defined]
        set_module("homeassistant.exceptions", exceptions_module)
        ha_module.exceptions = exceptions_module  # type: ignore[attr-defined]

        helpers_module = types.ModuleType("homeassistant.helpers")
        set_module("homeassistant.helpers", helpers_module)
        ha_module.helpers = helpers_module  # type: ignore[attr-defined]

        selector_module = types.ModuleType("homeassistant.helpers.selector")

        class _FileSelectorConfig:  # pragma: no cover - хранит параметры селектора
            def __init__(self, *, accept: list[str] | None = None, multiple: bool = False) -> None:
                self.accept = accept or []
                self.multiple = multiple

        class _FileSelector:  # pragma: no cover - совместим с API HA
            def __init__(self, config: _FileSelectorConfig) -> None:
                self.config = config

            def __voluptuous_compile__(self, _: Any) -> Any:
                return lambda _path, value: value

        selector_module.FileSelectorConfig = _FileSelectorConfig  # type: ignore[attr-defined]
        selector_module.FileSelector = _FileSelector  # type: ignore[attr-defined]
        set_module("homeassistant.helpers.selector", selector_module)
        helpers_module.selector = selector_module  # type: ignore[attr-defined]

        aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")

        async def _async_get_clientsession(_hass: Any) -> object:  # pragma: no cover - не используется
            return object()

        aiohttp_client_module.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
        set_module("homeassistant.helpers.aiohttp_client", aiohttp_client_module)
        helpers_module.aiohttp_client = aiohttp_client_module  # type: ignore[attr-defined]

        event_module = types.ModuleType("homeassistant.helpers.event")

        def _async_track_time_interval(*args: Any, **kwargs: Any):  # pragma: no cover - заглушка
            return lambda: None

        event_module.async_track_time_interval = _async_track_time_interval  # type: ignore[attr-defined]
        set_module("homeassistant.helpers.event", event_module)
        helpers_module.event = event_module  # type: ignore[attr-defined]

        # --- Пакет custom_components ---
        custom_components_module = types.ModuleType("custom_components")
        custom_components_module.__path__ = [str(PACKAGE_ROOT.parent)]  # type: ignore[attr-defined]
        set_module("custom_components", custom_components_module)

        intersvyaz_module = types.ModuleType("custom_components.intersvyaz")
        intersvyaz_module.__path__ = [str(PACKAGE_ROOT)]  # type: ignore[attr-defined]
        set_module("custom_components.intersvyaz", intersvyaz_module)

        # Подгружаем const.py как настоящий модуль, он не зависит от Home Assistant.
        const_spec = spec_from_file_location(
            "custom_components.intersvyaz.const", PACKAGE_ROOT / "const.py"
        )
        const_module = module_from_spec(const_spec)
        set_module("custom_components.intersvyaz.const", const_module)
        assert const_spec.loader is not None
        const_spec.loader.exec_module(const_module)

        # Минимальные заглушки API, чтобы загрузка config_flow прошла успешно.
        api_module = types.ModuleType("custom_components.intersvyaz.api")

        class ConfirmAddress:  # pragma: no cover - структура для select_account
            def __init__(self, user_id: str, address: str) -> None:
                self.user_id = user_id
                self.address = address

        class IntersvyazApiClient:  # pragma: no cover - не используется в тестах
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        class IntersvyazApiError(Exception):
            pass

        class MobileToken:  # pragma: no cover - совместимость с аннотациями
            pass

        class RelayInfo:  # pragma: no cover - совместимость с аннотациями
            pass

        def generate_device_id() -> str:  # pragma: no cover - заглушка генератора
            return "device-id"

        api_module.ConfirmAddress = ConfirmAddress  # type: ignore[attr-defined]
        api_module.IntersvyazApiClient = IntersvyazApiClient  # type: ignore[attr-defined]
        api_module.IntersvyazApiError = IntersvyazApiError  # type: ignore[attr-defined]
        api_module.MobileToken = MobileToken  # type: ignore[attr-defined]
        api_module.RelayInfo = RelayInfo  # type: ignore[attr-defined]
        api_module.generate_device_id = generate_device_id  # type: ignore[attr-defined]
        set_module("custom_components.intersvyaz.api", api_module)

        # Заглушка менеджера распознавания лиц, подчёркивающая сценарий без библиотеки.
        face_manager_module = types.ModuleType("custom_components.intersvyaz.face_manager")
        const = const_module

        class FaceRecognitionManager:  # pragma: no cover - логика адаптирована под тесты
            def __init__(self, hass: Any, entry: Any, **_: Any) -> None:
                self._entry = entry
                self._names: list[str] = []
                # Используем атрибут, чтобы управлять доступностью библиотеки в тестах.
                self._library_available = library_available
                stored = entry.options.get(const.CONF_KNOWN_FACES, [])
                for item in stored:
                    if isinstance(item, dict) and const.CONF_FACE_NAME in item:
                        self._names.append(str(item[const.CONF_FACE_NAME]))
                self.add_calls: list[tuple[str, bytes]] = []
                self.remove_calls: list[str] = []

            @property
            def library_available(self) -> bool:
                return self._library_available

            def list_known_face_names(self) -> list[str]:
                return list(self._names)

            async def async_add_known_face(self, name: str, image_bytes: bytes) -> None:
                self.add_calls.append((name, image_bytes))
                self._names = [item for item in self._names if item != name] + [name]

            async def async_remove_known_face(self, name: str) -> None:
                if name in self._names:
                    self._names.remove(name)
                    self.remove_calls.append(name)
                else:
                    raise Exception("not found")

        face_manager_module.FaceRecognitionManager = FaceRecognitionManager  # type: ignore[attr-defined]
        set_module("custom_components.intersvyaz.face_manager", face_manager_module)

        # Наконец, загружаем config_flow.py с подготовленным окружением.
        config_flow_spec = spec_from_file_location(
            "custom_components.intersvyaz.config_flow", PACKAGE_ROOT / "config_flow.py"
        )
        config_flow_module = module_from_spec(config_flow_spec)
        set_module("custom_components.intersvyaz.config_flow", config_flow_module)
        assert config_flow_spec.loader is not None
        config_flow_spec.loader.exec_module(config_flow_module)

        yield config_flow_module
    finally:
        # Возвращаем оригинальные модули в sys.modules.
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class _DummyConfigEntry:
    """Минимальная заглушка конфигурационной записи Home Assistant."""

    def __init__(self) -> None:
        self.entry_id = "test-entry"
        self.options: Dict[str, Any] = {}


class _DummyHass:
    """Упрощённый объект Home Assistant для тестов options flow."""

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        async def _update_entry(entry: Any, *, data: Any | None = None, options: Any | None = None):
            if options is not None:
                entry.options = options
            if data is not None:
                entry.data = data
            return True

        self.config_entries = types.SimpleNamespace(
            async_update_entry=AsyncMock(side_effect=_update_entry)
        )

    async def async_add_executor_job(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def test_options_flow_menu_shows_known_faces_placeholder() -> None:
    """Меню настроек отображает подсказку, когда база лиц пуста."""

    async def _run() -> None:
        with _load_config_flow_module(library_available=False) as module:
            entry = _DummyConfigEntry()
            flow = module.IntersvyazOptionsFlow(entry)
            flow.hass = _DummyHass()

            result = await flow.async_step_init()

        assert result["type"] == "menu"
        assert result["step_id"] == "init"
        assert result["menu_options"] == {"add_face": "add_face"}
        assert (
            "Пока не добавлено ни одного лица"
            in result["description_placeholders"]["known_faces"]
        )

    asyncio.run(_run())


def test_options_flow_add_face_requires_library() -> None:
    """Добавление лица без установленной библиотеки выдаёт ошибку."""

    async def _run() -> None:
        with _load_config_flow_module(library_available=False) as module:
            entry = _DummyConfigEntry()
            flow = module.IntersvyazOptionsFlow(entry)
            flow.hass = _DummyHass()

            await flow.async_step_init()
            result = await flow.async_step_add_face()

        assert result["type"] == "form"
        assert result["errors"]["base"] == "library_missing"

    asyncio.run(_run())


def test_options_flow_add_face_with_fallback_upload() -> None:
    """При работе через UploadFileFallback изображение должно попадать в менеджер."""

    async def _run() -> None:
        with _load_config_flow_module(library_available=True) as module:
            entry = _DummyConfigEntry()
            flow = module.IntersvyazOptionsFlow(entry)
            flow.hass = _DummyHass()

            await flow.async_step_init()
            module.HAS_NATIVE_UPLOAD_FILE = False
            module.UploadFile = module._UploadFileFallback  # type: ignore[attr-defined]

            upload = module.UploadFile(b"image-bytes")
            user_input = {
                module.CONF_FACE_NAME: "Гость",
                module.CONF_FACE_IMAGE: upload,
            }

            result = await flow.async_step_add_face(user_input)

            assert result["type"] == "create_entry"
            manager = flow.hass.data[module.DOMAIN][entry.entry_id][module.DATA_FACE_MANAGER]
            assert manager.add_calls == [("Гость", b"image-bytes")]

    asyncio.run(_run())


def test_options_flow_remove_face_updates_manager() -> None:
    """Удаление лица должно вызывать соответствующий метод менеджера."""

    async def _run() -> None:
        with _load_config_flow_module(library_available=True) as module:
            entry = _DummyConfigEntry()
            entry.options[module.CONF_KNOWN_FACES] = [{module.CONF_FACE_NAME: "Гость"}]
            flow = module.IntersvyazOptionsFlow(entry)
            flow.hass = _DummyHass()

            await flow.async_step_init()
            intermediate = await flow.async_step_remove_face()
            assert intermediate["type"] == "form"

            result = await flow.async_step_remove_face({module.CONF_FACE_NAME: "Гость"})

            assert result["type"] == "create_entry"
            manager = flow.hass.data[module.DOMAIN][entry.entry_id][module.DATA_FACE_MANAGER]
            assert manager.remove_calls == ["Гость"]

    asyncio.run(_run())


def test_options_flow_background_cameras_updates_options() -> None:
    """Настройка фоновых камер обновляет опции записи."""

    async def _run() -> None:
        with _load_config_flow_module(library_available=True) as module:
            entry = _DummyConfigEntry()
            flow = module.IntersvyazOptionsFlow(entry)
            hass = _DummyHass()
            hass.data = {
                module.DOMAIN: {
                    entry.entry_id: {
                        module.DATA_DOOR_OPENERS: [
                            {
                                "uid": "door-1",
                                "address": "Первый",
                                "has_video": True,
                                "image_url": "https://snapshots/door-1.jpg",
                                "is_main": True,
                            },
                            {
                                "uid": "door-2",
                                "address": "Второй",
                                "has_video": True,
                                "image_url": "https://snapshots/door-2.jpg",
                            },
                        ]
                    }
                }
            }
            flow.hass = hass

            await flow.async_step_init()
            form = await flow.async_step_background_cameras()
            assert form["type"] == "form"

            user_input = {module.CONF_BACKGROUND_CAMERAS: ["door-2"]}
            result = await flow.async_step_background_cameras(user_input)

            assert result["type"] == "create_entry"
            assert hass.config_entries.async_update_entry.await_count == 1
            assert entry.options[module.CONF_BACKGROUND_CAMERAS] == ["door-2"]

    asyncio.run(_run())
