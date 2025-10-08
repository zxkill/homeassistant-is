"""Тесты проверяют метаданные интеграции на совместимость с HACS."""
from __future__ import annotations

import json
from pathlib import Path
import types

import pytest


@pytest.fixture
def repo_root() -> Path:
    """Вернуть корневую директорию репозитория."""

    # Используем Path(__file__) для определения расположения проекта.
    return Path(__file__).resolve().parent.parent


def test_hacs_manifest_matches_integration(repo_root: Path) -> None:
    """Убеждаемся, что файл hacs.json описывает домен интеграции корректно."""

    hacs_file = repo_root / "hacs.json"
    manifest_file = repo_root / "custom_components" / "intersvyaz" / "manifest.json"

    assert hacs_file.exists(), "Файл hacs.json обязателен для установки через HACS"
    assert manifest_file.exists(), "Файл manifest.json обязателен для интеграции"

    hacs_data = json.loads(hacs_file.read_text(encoding="utf-8"))
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    # Проверяем, что домен из manifest.json присутствует в списке доменов hacs.json.
    assert manifest_data["domain"] in hacs_data["domains"], (
        "Домен из manifest.json должен быть перечислен в hacs.json"
    )

    # HACS ожидает соответствия версии manifest.json и тегов релизов; базовая проверка на наличие версии.
    assert isinstance(manifest_data.get("version"), str) and manifest_data["version"], (
        "manifest.json обязан содержать версию для корректной работы обновлений в HACS"
    )


def test_integration_importable(repo_root: Path) -> None:
    """Проверяем, что пакет custom_components.intersvyaz корректно импортируется."""

    import importlib
    import sys

    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Создаем минимальные заглушки для пакетов Home Assistant, чтобы импорт прошел успешно.
    homeassistant_module = types.ModuleType("homeassistant")
    components: dict[str, types.ModuleType] = {}

    def ensure_module(name: str) -> types.ModuleType:
        module = components.get(name)
        if module is None:
            module = types.ModuleType(name)
            components[name] = module
            sys.modules[name] = module
        return module

    sys.modules.setdefault("homeassistant", homeassistant_module)
    config_entries_module = ensure_module("homeassistant.config_entries")
    core_module = ensure_module("homeassistant.core")
    helpers_module = ensure_module("homeassistant.helpers")
    aiohttp_client_module = ensure_module("homeassistant.helpers.aiohttp_client")
    update_coordinator_module = ensure_module("homeassistant.helpers.update_coordinator")
    const_module = ensure_module("homeassistant.const")
    exceptions_module = ensure_module("homeassistant.exceptions")
    voluptuous_module = ensure_module("voluptuous")
    aiohttp_module = ensure_module("aiohttp")

    # Добавляем минимальные объекты, которые использует интеграция.
    class _ConfigEntry:  # pragma: no cover - класс используется только как заглушка
        pass

    class _HomeAssistant:  # pragma: no cover - заглушка для типа HomeAssistant
        config_entries = types.SimpleNamespace(async_update_entry=lambda *args, **kwargs: None)
        services = types.SimpleNamespace(async_register=lambda *args, **kwargs: None, async_remove=lambda *args, **kwargs: None)
        data: dict[str, dict] = {}

    class _ServiceCall:  # pragma: no cover - заглушка для ServiceCall
        pass

    def _async_get_clientsession(*_args, **_kwargs):  # pragma: no cover - заглушка
        raise RuntimeError("aiohttp не доступен в тестовой среде")

    config_entries_module.ConfigEntry = _ConfigEntry
    core_module.HomeAssistant = _HomeAssistant
    core_module.ServiceCall = _ServiceCall
    helpers_module.aiohttp_client = aiohttp_client_module
    aiohttp_client_module.async_get_clientsession = _async_get_clientsession
    const_module.Platform = types.SimpleNamespace(SENSOR="sensor", BUTTON="button")

    class _HomeAssistantError(Exception):  # pragma: no cover - заглушка ошибки
        pass

    exceptions_module.HomeAssistantError = _HomeAssistantError

    def _cv_string(value):  # pragma: no cover - простая имитация cv.string
        return value

    helpers_module.config_validation = types.SimpleNamespace(string=_cv_string)
    helpers_module.update_coordinator = update_coordinator_module

    class _Schema:  # pragma: no cover - простая заглушка Schema
        def __init__(self, _schema: object) -> None:
            self._schema = _schema

        def __call__(self, data: object) -> object:
            return data

    def _required(key: str) -> str:  # pragma: no cover - заглушка Required
        return key

    def _optional(key: str) -> str:  # pragma: no cover - заглушка Optional
        return key

    def _in(options: object) -> object:  # pragma: no cover - заглушка In
        return options

    voluptuous_module.Schema = _Schema  # type: ignore[attr-defined]
    voluptuous_module.Required = _required  # type: ignore[attr-defined]
    voluptuous_module.Optional = _optional  # type: ignore[attr-defined]
    voluptuous_module.In = _in  # type: ignore[attr-defined]

    class _ClientError(Exception):  # pragma: no cover - заглушка aiohttp.ClientError
        pass

    class _ClientResponse:  # pragma: no cover - используется только для аннотаций
        pass

    class _ClientSession:  # pragma: no cover - базовая заглушка клиента
        pass

    aiohttp_module.ClientError = _ClientError  # type: ignore[attr-defined]
    aiohttp_module.ClientResponse = _ClientResponse  # type: ignore[attr-defined]
    aiohttp_module.ClientSession = _ClientSession  # type: ignore[attr-defined]

    class _DataUpdateCoordinator:  # pragma: no cover - простая заглушка
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __class_getitem__(cls, _item):  # type: ignore[override]
            return cls

    class _UpdateFailed(Exception):  # pragma: no cover - заглушка ошибки
        pass

    update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
    update_coordinator_module.UpdateFailed = _UpdateFailed

    # Удаляем подмены из других тестов, чтобы получить настоящий пакет интеграции.
    for name in list(sys.modules):
        if name == "custom_components" or name.startswith("custom_components.intersvyaz"):
            sys.modules.pop(name)

    module = importlib.import_module("custom_components.intersvyaz")
    assert hasattr(module, "async_setup_entry"), (
        "Интеграция должна предоставлять функцию async_setup_entry"
    )
    assert hasattr(module, "async_unload_entry"), (
        "Интеграция должна предоставлять функцию async_unload_entry"
    )
