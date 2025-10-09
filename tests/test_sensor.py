"""Тесты совместимости сенсоров Intersvyaz с разными версиями Home Assistant."""
from __future__ import annotations

import enum
import logging
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Iterator

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz"


@contextmanager
def _load_sensor_module(*, has_enum: bool, has_legacy_const: bool) -> Iterator[types.ModuleType]:
    """Загрузить модуль сенсора с заглушками Home Assistant и вернуть его."""

    saved_modules: dict[str, types.ModuleType | None] = {}

    def set_module(name: str, module: types.ModuleType) -> None:
        """Сохранить оригинальный модуль и подменить его заглушкой."""

        if name not in saved_modules:
            saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = module

    # Подготовка пакета homeassistant и необходимых подмодулей с минимальными
    # реализациями, чтобы импорт sensor.py прошел без настоящих зависимостей.
    ha_module = types.ModuleType("homeassistant")
    set_module("homeassistant", ha_module)

    const_module = types.ModuleType("homeassistant.const")
    if has_enum:
        class _UnitOfCurrency(enum.Enum):  # type: ignore[too-few-public-methods]
            RUBLE = "₽"

        const_module.UnitOfCurrency = _UnitOfCurrency  # type: ignore[attr-defined]
    if has_legacy_const:
        const_module.CURRENCY_RUB = "₽"  # type: ignore[attr-defined]
    class _Platform(enum.Enum):  # pragma: no cover - минимальный набор платформ
        SENSOR = "sensor"
        BUTTON = "button"

    const_module.Platform = _Platform  # type: ignore[attr-defined]
    set_module("homeassistant.const", const_module)
    ha_module.const = const_module  # type: ignore[attr-defined]

    components_module = types.ModuleType("homeassistant.components")
    sensor_module = types.ModuleType("homeassistant.components.sensor")

    class _SensorEntity:  # pragma: no cover - пустая заглушка
        pass

    sensor_module.SensorDeviceClass = types.SimpleNamespace(MONETARY="monetary")
    sensor_module.SensorEntity = _SensorEntity
    sensor_module.SensorStateClass = types.SimpleNamespace(MEASUREMENT="measurement")
    components_module.sensor = sensor_module  # type: ignore[attr-defined]
    set_module("homeassistant.components", components_module)
    set_module("homeassistant.components.sensor", sensor_module)

    config_entries_module = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:  # pragma: no cover - используется только как тип
        entry_id = "test"

    config_entries_module.ConfigEntry = _ConfigEntry  # type: ignore[attr-defined]
    set_module("homeassistant.config_entries", config_entries_module)

    core_module = types.ModuleType("homeassistant.core")

    class _HomeAssistant:  # pragma: no cover - пустая заглушка
        pass

    core_module.HomeAssistant = _HomeAssistant  # type: ignore[attr-defined]
    set_module("homeassistant.core", core_module)

    helpers_module = types.ModuleType("homeassistant.helpers")
    device_registry_module = types.ModuleType("homeassistant.helpers.device_registry")

    class _DeviceEntryType(enum.Enum):  # pragma: no cover - достаточно перечисления
        SERVICE = "service"

    class _DeviceInfo:  # pragma: no cover - простая структура для совместимости
        def __init__(self, **kwargs: object) -> None:
            self.data = kwargs

    device_registry_module.DeviceEntryType = _DeviceEntryType  # type: ignore[attr-defined]
    device_registry_module.DeviceInfo = _DeviceInfo  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.device_registry", device_registry_module)
    helpers_module.device_registry = device_registry_module  # type: ignore[attr-defined]

    entity_platform_module = types.ModuleType("homeassistant.helpers.entity_platform")

    def _add_entities_stub(*_args, **_kwargs) -> None:  # pragma: no cover - заглушка
        return None

    entity_platform_module.AddEntitiesCallback = _add_entities_stub  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.entity_platform", entity_platform_module)

    update_coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _CoordinatorEntity:  # pragma: no cover - минимальная реализация миксина
        def __init__(self, coordinator: object) -> None:
            self.coordinator = coordinator

    update_coordinator_module.CoordinatorEntity = _CoordinatorEntity  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.update_coordinator", update_coordinator_module)
    helpers_module.update_coordinator = update_coordinator_module  # type: ignore[attr-defined]

    aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def _async_get_clientsession(_hass: object) -> object:  # pragma: no cover - заглушка
        return object()

    aiohttp_client_module.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.aiohttp_client", aiohttp_client_module)

    config_validation_module = types.ModuleType("homeassistant.helpers.config_validation")

    def _cv_string(value: object) -> object:  # pragma: no cover - заглушка cv.string
        return value

    config_validation_module.string = _cv_string  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.config_validation", config_validation_module)

    exceptions_module = types.ModuleType("homeassistant.exceptions")

    class _HomeAssistantError(Exception):  # pragma: no cover - заглушка ошибки
        pass

    exceptions_module.HomeAssistantError = _HomeAssistantError  # type: ignore[attr-defined]
    set_module("homeassistant.exceptions", exceptions_module)

    helpers_module.aiohttp_client = aiohttp_client_module  # type: ignore[attr-defined]
    helpers_module.config_validation = config_validation_module  # type: ignore[attr-defined]
    helpers_module.update_coordinator = update_coordinator_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers", helpers_module)

    try:
        const_spec = spec_from_file_location(
            "custom_components.intersvyaz.const", PACKAGE_ROOT / "const.py"
        )
        assert const_spec and const_spec.loader
        const_module_loaded = module_from_spec(const_spec)
        set_module("custom_components.intersvyaz.const", const_module_loaded)
        const_spec.loader.exec_module(const_module_loaded)  # type: ignore[union-attr]

        spec = spec_from_file_location(
            "custom_components.intersvyaz.sensor", PACKAGE_ROOT / "sensor.py"
        )
        assert spec and spec.loader
        module = module_from_spec(spec)
        sys.modules["custom_components.intersvyaz.sensor"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        yield module
    finally:
        sys.modules.pop("custom_components.intersvyaz.sensor", None)
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_resolve_balance_unit_prefers_enum(caplog: pytest.LogCaptureFixture) -> None:
    """Проверяем, что при наличии UnitOfCurrency выбирается перечисление."""

    caplog.set_level(logging.DEBUG, logger="intersvyaz.sensor")
    with _load_sensor_module(has_enum=True, has_legacy_const=True) as module:
        balance_unit = module.BALANCE_UNIT
        assert getattr(balance_unit, "name", None) == "RUBLE"

    assert "UnitOfCurrency.RUBLE" in caplog.text


def test_resolve_balance_unit_uses_legacy_constant(caplog: pytest.LogCaptureFixture) -> None:
    """Проверяем, что при отсутствии UnitOfCurrency используется строковая константа."""

    caplog.set_level(logging.DEBUG, logger="intersvyaz.sensor")
    with _load_sensor_module(has_enum=False, has_legacy_const=True) as module:
        balance_unit = module.BALANCE_UNIT
        assert balance_unit == "₽"

    assert "строковую единицу" in caplog.text


def test_resolve_balance_unit_falls_back_to_rub() -> None:
    """Проверяем, что в крайнем случае возвращается стандартный код RUB."""

    with _load_sensor_module(has_enum=False, has_legacy_const=False) as module:
        assert module.BALANCE_UNIT == "RUB"


def test_balance_sensor_does_not_use_measurement_state_class() -> None:
    """Убеждаемся, что денежный сенсор не устанавливает запрещённый state_class."""

    with _load_sensor_module(has_enum=True, has_legacy_const=True) as module:
        coordinator = types.SimpleNamespace(data={"user": {}})
        entry = types.SimpleNamespace(entry_id="test")
        sensor = module.IntersvyazBalanceSensor(coordinator, entry)
        # Проверяем именно защищённый атрибут, потому что в заглушке SensorEntity
        # отсутствует свойство state_class. Это гарантирует соответствие
        # требованиям Home Assistant и отсутствие предупреждений в логах.
        assert getattr(sensor, "_attr_state_class") is None

