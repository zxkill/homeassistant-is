from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Dict
import sys
import types

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz"


def _load_module(module_name: str, relative_path: str):
    """Загрузить модуль интеграции с подменой путей."""

    spec = spec_from_file_location(module_name, PACKAGE_ROOT / relative_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Заглушки Home Assistant для импорта config_flow
homeassistant_module = types.ModuleType("homeassistant")
config_entries_module = types.ModuleType("homeassistant.config_entries")
helpers_module = types.ModuleType("homeassistant.helpers")
core_module = types.ModuleType("homeassistant.core")
data_entry_flow_module = types.ModuleType("homeassistant.data_entry_flow")
aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")


class _DummyConfigFlow:
    """Минимальная заглушка ConfigFlow."""

    def __init_subclass__(cls, **kwargs):  # type: ignore[override]
        super().__init_subclass__()


def _callback(func):
    return func


def _async_get_clientsession(_hass: Any) -> Any:  # pragma: no cover
    raise RuntimeError("aiohttp клиент не используется в юнит-тестах")


data_entry_flow_module.FlowResult = Dict[str, Any]
config_entries_module.ConfigFlow = _DummyConfigFlow
core_module.callback = _callback
helpers_module.aiohttp_client = aiohttp_client_module
aiohttp_client_module.async_get_clientsession = _async_get_clientsession

homeassistant_module.config_entries = config_entries_module
homeassistant_module.core = core_module
homeassistant_module.data_entry_flow = data_entry_flow_module
homeassistant_module.helpers = helpers_module

sys.modules.setdefault("homeassistant", homeassistant_module)
sys.modules.setdefault("homeassistant.config_entries", config_entries_module)
sys.modules.setdefault("homeassistant.helpers", helpers_module)
sys.modules.setdefault("homeassistant.core", core_module)
sys.modules.setdefault("homeassistant.data_entry_flow", data_entry_flow_module)
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", aiohttp_client_module)

CONFIG_FLOW_MODULE = _load_module("custom_components.intersvyaz.config_flow", "config_flow.py")


def test_normalize_message() -> None:
    """HTML сообщения преобразуются в читабельный текст."""

    raw = "Сейчас на номер<br>+7 (900) 111-22-33 позвонят.<br>Введите код"
    normalized = CONFIG_FLOW_MODULE._normalize_message(raw)
    assert normalized == "Сейчас на номер\n+7 (900) 111-22-33 позвонят.\nВведите код"


def test_validate_mac() -> None:
    """Проверяем корректность валидации MAC-адресов."""

    assert CONFIG_FLOW_MODULE._validate_mac("08:53:CD:00:83:4E")
    assert not CONFIG_FLOW_MODULE._validate_mac("invalid-mac")


def test_build_description_placeholders() -> None:
    """Подсказка использует текст оператора и ошибки."""

    flow = CONFIG_FLOW_MODULE.IntersvyazConfigFlow()
    flow.hass = types.SimpleNamespace(config=types.SimpleNamespace(language="ru-RU"))
    flow._confirm_message = "Ожидайте звонок"
    flow._last_error_message = "Неверный код подтверждения"
    placeholders = flow._build_description_placeholders()
    assert "Ожидайте звонок" in placeholders["auth_message"]
    assert "Неверный код подтверждения" in placeholders["auth_message"]


def test_select_preferred_relay() -> None:
    """Выбор домофона отдаёт приоритет основному входу."""

    RelayInfo = CONFIG_FLOW_MODULE.RelayInfo
    main_relay = RelayInfo(
        address="Основной вход",
        relay_id="1",
        status_code="0",
        building_id=None,
        mac="08:13:CD:00:0D:7A",
        status_text="OK",
        is_main=True,
        has_video=True,
        entrance_uid=None,
        porch_num="1",
        relay_type="Главный",
        relay_descr=None,
        smart_intercom=None,
        num_building=None,
        letter_building=None,
        image_url=None,
        open_link=None,
        opener=None,
        raw={},
    )
    secondary_relay = RelayInfo(
        address="Ворота",
        relay_id="2",
        status_code="0",
        building_id=None,
        mac="08:13:CD:00:0D:7B",
        status_text="OK",
        is_main=False,
        has_video=False,
        entrance_uid=None,
        porch_num="2",
        relay_type="Ворота",
        relay_descr=None,
        smart_intercom=None,
        num_building=None,
        letter_building=None,
        image_url=None,
        open_link=None,
        opener=None,
        raw={},
    )

    selected = CONFIG_FLOW_MODULE._select_preferred_relay(
        [secondary_relay, main_relay]
    )
    assert selected is main_relay


def test_coerce_buyer_id_logs_warning_for_non_default(caplog: pytest.LogCaptureFixture) -> None:
    """Любые отличные от единицы кандидаты логируются и заменяются на стандартное значение."""

    RelayInfo = CONFIG_FLOW_MODULE.RelayInfo
    MobileToken = CONFIG_FLOW_MODULE.MobileToken
    relay = RelayInfo(
        address="Основной вход",
        relay_id="50001",
        status_code="0",
        building_id=None,
        mac="08:13:CD:00:0D:7A",
        status_text="OK",
        is_main=True,
        has_video=True,
        entrance_uid=None,
        porch_num="1",
        relay_type="Главный",
        relay_descr=None,
        smart_intercom=None,
        num_building=None,
        letter_building=None,
        image_url=None,
        open_link=None,
        opener=None,
        raw={},
    )
    token = MobileToken(
        token="test-token",
        user_id=1,
        profile_id=777,
        access_begin=None,
        access_end=None,
        phone=None,
        unique_device_id=None,
        raw={},
    )

    with caplog.at_level("WARNING"):
        buyer_id = CONFIG_FLOW_MODULE._coerce_buyer_id(relay, token)

    assert buyer_id == CONFIG_FLOW_MODULE.DEFAULT_BUYER_ID
    assert "CRM ожидает buyer_id" in caplog.text


def test_coerce_buyer_id_debugs_when_candidates_already_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Если API даёт единицу, фиксируем это в debug-логе для диагностики."""

    RelayInfo = CONFIG_FLOW_MODULE.RelayInfo
    MobileToken = CONFIG_FLOW_MODULE.MobileToken
    relay = RelayInfo(
        address="Основной вход",
        relay_id="1",
        status_code="0",
        building_id=None,
        mac="08:13:CD:00:0D:7A",
        status_text="OK",
        is_main=True,
        has_video=True,
        entrance_uid=None,
        porch_num="1",
        relay_type="Главный",
        relay_descr=None,
        smart_intercom=None,
        num_building=None,
        letter_building=None,
        image_url=None,
        open_link=None,
        opener=None,
        raw={},
    )
    token = MobileToken(
        token="test-token",
        user_id=1,
        profile_id=1,
        access_begin=None,
        access_end=None,
        phone=None,
        unique_device_id=None,
        raw={},
    )

    with caplog.at_level("DEBUG"):
        buyer_id = CONFIG_FLOW_MODULE._coerce_buyer_id(relay, token)

    assert buyer_id == CONFIG_FLOW_MODULE.DEFAULT_BUYER_ID
    assert "CRM использует buyer_id" in caplog.text
