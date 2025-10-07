"""Тестирование утилитарной логики конфигурационного потока."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Dict

import sys
import types

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz"


def _load_module(module_name: str, relative_path: str):
    """Загрузить модуль интеграции с подменой путей."""

    spec = spec_from_file_location(module_name, PACKAGE_ROOT / relative_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Подготавливаем минимальные заглушки Home Assistant, чтобы импорт config_flow прошёл успешно.
homeassistant_module = types.ModuleType("homeassistant")
config_entries_module = types.ModuleType("homeassistant.config_entries")
helpers_module = types.ModuleType("homeassistant.helpers")
helpers_module.__path__ = []  # type: ignore[attr-defined]
core_module = types.ModuleType("homeassistant.core")
data_entry_flow_module = types.ModuleType("homeassistant.data_entry_flow")
aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")


class _DummyConfigFlow:  # pragma: no cover - в тестах используется только статический метод
    """Минимальная заглушка ConfigFlow."""

    def __init_subclass__(cls, **kwargs):  # type: ignore[override]
        """Игнорировать дополнительные аргументы при наследовании."""

        super().__init_subclass__()


def _callback(func):
    """Имитация декоратора callback Home Assistant."""

    return func


def _async_get_clientsession(hass: Any) -> Any:  # pragma: no cover - не используется в тестах
    """Простая заглушка для клиента aiohttp."""

    raise RuntimeError("Заглушка aiohttp клиента не должна вызываться в юнит-тестах")


data_entry_flow_module.FlowResult = Dict[str, Any]
config_entries_module.ConfigFlow = _DummyConfigFlow
core_module.callback = _callback
helpers_module.aiohttp = types.ModuleType("homeassistant.helpers.aiohttp")
aiohttp_client_module.async_get_clientsession = _async_get_clientsession

homeassistant_module.config_entries = config_entries_module
homeassistant_module.core = core_module
homeassistant_module.data_entry_flow = data_entry_flow_module
homeassistant_module.helpers = helpers_module
helpers_module.aiohttp_client = aiohttp_client_module

sys.modules.setdefault("homeassistant", homeassistant_module)
sys.modules.setdefault("homeassistant.config_entries", config_entries_module)
sys.modules.setdefault("homeassistant.helpers", helpers_module)
sys.modules.setdefault("homeassistant.core", core_module)
sys.modules.setdefault("homeassistant.data_entry_flow", data_entry_flow_module)
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", aiohttp_client_module)

CONFIG_FLOW_MODULE = _load_module("custom_components.intersvyaz.config_flow", "config_flow.py")


def test_extract_user_message_success() -> None:
    """Проверить корректную очистку HTML сообщения."""

    context = {
        "message": "Сейчас на номер<br>+7 (908) 048-57-43 позвонят.<br>Введите код",
    }
    cleaned = CONFIG_FLOW_MODULE.IntersvyazConfigFlow._extract_user_message(context)
    assert cleaned == "Сейчас на номер\n+7 (908) 048-57-43 позвонят.\nВведите код"


def test_extract_user_message_invalid_payload() -> None:
    """Убедиться, что при отсутствии сообщения возвращается None."""

    assert CONFIG_FLOW_MODULE.IntersvyazConfigFlow._extract_user_message(None) is None
    assert (
        CONFIG_FLOW_MODULE.IntersvyazConfigFlow._extract_user_message({"message": 123})
        is None
    )


def test_build_description_placeholders_with_error_message() -> None:
    """Проверить формирование плейсхолдеров и локализацию ошибок."""

    flow = CONFIG_FLOW_MODULE.IntersvyazConfigFlow()
    flow.hass = types.SimpleNamespace(
        config=types.SimpleNamespace(language="ru-RU")
    )
    flow._auth_message = "Сообщение оператора"
    flow._last_error_message = "Неверный код подтверждения"

    placeholders = flow._build_description_placeholders()

    assert placeholders["auth_message"].startswith("Сообщение оператора")
    assert "Ошибка" in placeholders["error_message"]
    assert "Неверный код подтверждения" in placeholders["error_message"]


def test_build_description_placeholders_without_message() -> None:
    """Убедиться, что при отсутствии подсказок используются значения по умолчанию."""

    flow = CONFIG_FLOW_MODULE.IntersvyazConfigFlow()
    flow.hass = types.SimpleNamespace(config=types.SimpleNamespace(language="en-US"))
    flow._auth_message = None
    flow._last_error_message = None

    placeholders = flow._build_description_placeholders()

    assert "Enter the confirmation code" in placeholders["auth_message"]
    assert placeholders["error_message"] == ""
