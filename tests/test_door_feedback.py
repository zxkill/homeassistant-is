"""Тесты обратной связи кнопки и сенсора статуса домофона."""
from __future__ import annotations

import asyncio
import enum
import sys
import types
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator
from unittest.mock import AsyncMock

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = PACKAGE_ROOT / "custom_components" / "intersvyaz"


@contextmanager
def _load_modules() -> Iterator[types.SimpleNamespace]:
    """Импортировать модули интеграции с минимальными заглушками Home Assistant."""

    saved_modules: dict[str, types.ModuleType | None] = {}

    def set_module(name: str, module: types.ModuleType) -> None:
        """Сохранить оригинальный модуль и зарегистрировать заглушку."""

        if name not in saved_modules:
            saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = module

    # Заглушки сторонних зависимостей
    voluptuous_module = types.ModuleType("voluptuous")

    class _Schema:  # pragma: no cover - простая реализация схемы
        def __init__(self, schema: object) -> None:
            self._schema = schema

        def __call__(self, value: object) -> object:
            return value

    class _Marker(str):  # pragma: no cover - плейсхолдер для Required/Optional
        pass

    def _required(key: str) -> str:
        return _Marker(key)

    def _optional(key: str) -> str:
        return _Marker(key)

    voluptuous_module.Schema = _Schema  # type: ignore[attr-defined]
    voluptuous_module.Required = _required  # type: ignore[attr-defined]
    voluptuous_module.Optional = _optional  # type: ignore[attr-defined]
    set_module("voluptuous", voluptuous_module)

    aiohttp_module = types.ModuleType("aiohttp")

    class _ClientSession:  # pragma: no cover - минимум для совместимости
        async def close(self) -> None:
            return None

    class _ClientResponse:  # pragma: no cover - заглушка HTTP-ответа
        status = 204

    class _ClientError(Exception):
        pass

    aiohttp_module.ClientSession = _ClientSession  # type: ignore[attr-defined]
    aiohttp_module.ClientResponse = _ClientResponse  # type: ignore[attr-defined]
    aiohttp_module.ClientError = _ClientError  # type: ignore[attr-defined]
    set_module("aiohttp", aiohttp_module)

    # Базовые пакеты Home Assistant
    ha_module = types.ModuleType("homeassistant")
    set_module("homeassistant", ha_module)

    const_module = types.ModuleType("homeassistant.const")

    class _Platform(enum.Enum):  # pragma: no cover - используется при настройке платформ
        SENSOR = "sensor"
        BUTTON = "button"

    class _UnitOfCurrency(enum.Enum):  # pragma: no cover - имитирует перечисление валют
        RUBLE = "RUB"

    const_module.Platform = _Platform  # type: ignore[attr-defined]
    const_module.UnitOfCurrency = _UnitOfCurrency  # type: ignore[attr-defined]
    const_module.CURRENCY_RUB = "RUB"  # type: ignore[attr-defined]
    ha_module.const = const_module  # type: ignore[attr-defined]
    set_module("homeassistant.const", const_module)

    # Конфигурационные записи
    config_entries_module = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:  # pragma: no cover - контейнер для entry_id
        def __init__(self, entry_id: str) -> None:
            self.entry_id = entry_id

    config_entries_module.ConfigEntry = _ConfigEntry  # type: ignore[attr-defined]
    set_module("homeassistant.config_entries", config_entries_module)

    # Ядро Home Assistant
    core_module = types.ModuleType("homeassistant.core")

    class _HomeAssistant:  # pragma: no cover - лёгкая имитация HA
        def __init__(self) -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.data: dict[str, dict[str, Any]] = {}
            self.bus = SimpleNamespace(async_fire=lambda *args, **kwargs: None)
            self.services = SimpleNamespace(
                has_service=lambda *args, **kwargs: False,
                async_register=lambda *args, **kwargs: None,
                async_remove=lambda *args, **kwargs: None,
            )
            self._dispatcher: dict[str, list[Callable[..., None]]] = {}

    class _ServiceCall:  # pragma: no cover - используется в сервисных тестах
        def __init__(self, data: dict[str, Any] | None = None) -> None:
            self.data = data or {}

    core_module.HomeAssistant = _HomeAssistant  # type: ignore[attr-defined]
    core_module.ServiceCall = _ServiceCall  # type: ignore[attr-defined]
    set_module("homeassistant.core", core_module)

    # Исключения HA
    exceptions_module = types.ModuleType("homeassistant.exceptions")

    class _HomeAssistantError(Exception):  # pragma: no cover - простая ошибка
        pass

    exceptions_module.HomeAssistantError = _HomeAssistantError  # type: ignore[attr-defined]
    set_module("homeassistant.exceptions", exceptions_module)

    # Компонент кнопки
    components_module = types.ModuleType("homeassistant.components")
    button_module = types.ModuleType("homeassistant.components.button")

    class _BaseEntity:  # pragma: no cover - общий предок для сущностей
        def __init__(self) -> None:
            self._state_write_count = 0
            self.hass: _HomeAssistant | None = None
            self._attr_available = True
            self._attr_extra_state_attributes: dict[str, Any] | None = None

        def async_write_ha_state(self) -> None:
            self._state_write_count += 1

        @property
        def available(self) -> bool:
            return getattr(self, "_attr_available", True)

        @property
        def extra_state_attributes(self) -> dict[str, Any] | None:
            return getattr(self, "_attr_extra_state_attributes", None)

        def async_on_remove(self, func: Callable[[], None]) -> None:
            callbacks = getattr(self, "_remove_callbacks", [])
            callbacks.append(func)
            self._remove_callbacks = callbacks

    class _ButtonEntity(_BaseEntity):  # pragma: no cover - базовый класс кнопки
        pass

    button_module.ButtonEntity = _ButtonEntity  # type: ignore[attr-defined]
    components_module.button = button_module  # type: ignore[attr-defined]
    set_module("homeassistant.components", components_module)
    set_module("homeassistant.components.button", button_module)

    # Компонент сенсоров
    sensor_component = types.ModuleType("homeassistant.components.sensor")

    class _SensorEntity(_BaseEntity):  # pragma: no cover - базовый сенсор
        pass

    sensor_component.SensorEntity = _SensorEntity  # type: ignore[attr-defined]
    sensor_component.SensorDeviceClass = types.SimpleNamespace(MONETARY="monetary")
    sensor_component.SensorStateClass = types.SimpleNamespace(MEASUREMENT="measurement")
    set_module("homeassistant.components.sensor", sensor_component)

    # Реестр устройств
    device_registry_module = types.ModuleType("homeassistant.helpers.device_registry")

    class _DeviceInfo:  # pragma: no cover - контейнер идентификаторов
        def __init__(self, **kwargs: Any) -> None:
            self.data = kwargs

    class _DeviceEntryType(enum.Enum):  # pragma: no cover - типы записей
        SERVICE = "service"

    device_registry_module.DeviceInfo = _DeviceInfo  # type: ignore[attr-defined]
    device_registry_module.DeviceEntryType = _DeviceEntryType  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.device_registry", device_registry_module)

    # Категории сущностей
    entity_module = types.ModuleType("homeassistant.helpers.entity")

    class _EntityCategory(enum.Enum):  # pragma: no cover - категории для сенсоров
        DIAGNOSTIC = "diagnostic"

    entity_module.EntityCategory = _EntityCategory  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.entity", entity_module)

    # Платформа сущностей
    entity_platform_module = types.ModuleType("homeassistant.helpers.entity_platform")

    def _add_entities_callback(*_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        return None

    entity_platform_module.AddEntitiesCallback = Callable  # type: ignore[attr-defined]
    entity_platform_module.async_add_entities = _add_entities_callback  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.entity_platform", entity_platform_module)

    # Dispatcher
    dispatcher_module = types.ModuleType("homeassistant.helpers.dispatcher")

    def async_dispatcher_connect(hass: _HomeAssistant, signal: str, callback: Callable[..., None]) -> Callable[[], None]:
        hass._dispatcher.setdefault(signal, []).append(callback)

        def _remove() -> None:
            hass._dispatcher.get(signal, []).remove(callback)

        return _remove

    def async_dispatcher_send(hass: _HomeAssistant, signal: str, *args: Any) -> None:
        for callback in list(hass._dispatcher.get(signal, [])):
            callback(*args)

    dispatcher_module.async_dispatcher_connect = async_dispatcher_connect  # type: ignore[attr-defined]
    dispatcher_module.async_dispatcher_send = async_dispatcher_send  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.dispatcher", dispatcher_module)

    # Update coordinator
    update_coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _CoordinatorEntity(_BaseEntity):  # pragma: no cover - базовый класс координатора
        def __init__(self, coordinator: object) -> None:
            super().__init__()
            self.coordinator = coordinator

        async def async_added_to_hass(self) -> None:  # pragma: no cover - совместимость
            return None

    class _DataUpdateCoordinator:  # pragma: no cover - имитация координатора обновлений
        def __init__(self, hass: _HomeAssistant, logger: Any, name: str, update_interval: Any) -> None:
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

    # Дополнительные вспомогательные модули
    helpers_module = types.ModuleType("homeassistant.helpers")
    helpers_module.device_registry = device_registry_module  # type: ignore[attr-defined]
    helpers_module.entity_platform = entity_platform_module  # type: ignore[attr-defined]
    helpers_module.update_coordinator = update_coordinator_module  # type: ignore[attr-defined]
    helpers_module.dispatcher = dispatcher_module  # type: ignore[attr-defined]

    config_validation_module = types.ModuleType("homeassistant.helpers.config_validation")

    def _cv_string(value: object) -> object:  # pragma: no cover - имитация cv.string
        return value

    config_validation_module.string = _cv_string  # type: ignore[attr-defined]
    helpers_module.config_validation = config_validation_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.config_validation", config_validation_module)

    aiohttp_client_module = types.ModuleType("homeassistant.helpers.aiohttp_client")

    async def _async_get_clientsession(_hass: _HomeAssistant) -> _ClientSession:  # pragma: no cover
        return _ClientSession()

    aiohttp_client_module.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
    helpers_module.aiohttp_client = aiohttp_client_module  # type: ignore[attr-defined]
    set_module("homeassistant.helpers.aiohttp_client", aiohttp_client_module)

    set_module("homeassistant.helpers", helpers_module)

    # Namespace-пакет custom_components
    custom_components_pkg = sys.modules.get("custom_components")
    if not custom_components_pkg:
        custom_components_pkg = types.ModuleType("custom_components")
        custom_components_pkg.__path__ = [str(PACKAGE_ROOT / "custom_components")]
        sys.modules["custom_components"] = custom_components_pkg

    modules = {"dispatcher": dispatcher_module}
    try:
        for name in ("const", "button", "sensor", "__init__"):
            spec = spec_from_file_location(
                f"custom_components.intersvyaz.{name}",
                INTEGRATION_ROOT / f"{name}.py",
            )
            assert spec and spec.loader
            module = module_from_spec(spec)
            sys.modules[f"custom_components.intersvyaz.{name}"] = module
            if name == "__init__":
                sys.modules["custom_components.intersvyaz"] = module  # type: ignore[assignment]
                modules["integration"] = module
            else:
                modules[name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        yield types.SimpleNamespace(**modules)
    finally:
        for name in ("const", "button", "sensor", "__init__"):
            sys.modules.pop(f"custom_components.intersvyaz.{name}", None)
        sys.modules.pop("custom_components.intersvyaz", None)
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_button_updates_shared_status_and_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кнопка обновляет общий статус и уведомляет подписчиков диспетчера."""

    with _load_modules() as modules:
        const = modules.const
        button_module = modules.button
        core_module = sys.modules["homeassistant.core"]
        config_module = sys.modules["homeassistant.config_entries"]
        hass = core_module.HomeAssistant()
        entry = config_module.ConfigEntry("entry-test")
        door_entry = {"uid": "door-1", "address": "Подъезд 1"}
        hass.data.setdefault(const.DOMAIN, {})[entry.entry_id] = {
            const.DATA_COORDINATOR: SimpleNamespace(),
            const.DATA_DOOR_OPENERS: [],
            const.DATA_OPEN_DOOR: None,
            const.DATA_DOOR_STATUSES: {},
        }
        monkeypatch.setattr(button_module, "BUTTON_STATUS_RESET_DELAY_SECONDS", 0)

        open_mock = AsyncMock()
        button = button_module.IntersvyazDoorOpenButton(
            hass,
            SimpleNamespace(),
            entry,
            open_mock,
            door_entry,
        )
        button.hass = hass

        received: list[dict[str, Any]] = []

        def _listener(entry_id: str, door_uid: str, payload: dict[str, Any]) -> None:
            received.append({"entry_id": entry_id, "door_uid": door_uid, "payload": payload})

        async def _scenario() -> None:
            remove = modules.dispatcher.async_dispatcher_connect(hass, const.SIGNAL_DOOR_STATUS_UPDATED, _listener)  # type: ignore[attr-defined]
            try:
                await button.async_press()
                await asyncio.sleep(0)
            finally:
                remove()

        asyncio.run(_scenario())

        assert open_mock.await_count == 1
        statuses = hass.data[const.DOMAIN][entry.entry_id][const.DATA_DOOR_STATUSES]
        stored = statuses["door-1"]
        assert stored[const.ATTR_STATUS_CODE] == const.DOOR_STATUS_READY
        assert stored[const.ATTR_STATUS_BUSY] is False
        assert stored[const.ATTR_STATUS_LABEL] == const.DOOR_STATUS_LABELS[const.DOOR_STATUS_READY]
        assert stored[const.ATTR_STATUS_ERROR] is None

        assert any(item["payload"][const.ATTR_STATUS_CODE] == const.DOOR_STATUS_OPENED for item in received)
        assert button.available is True


def test_button_error_status_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """При ошибке хранится текст ошибки, а статус возвращается к «Готово»."""

    with _load_modules() as modules:
        const = modules.const
        button_module = modules.button
        core_module = sys.modules["homeassistant.core"]
        config_module = sys.modules["homeassistant.config_entries"]
        hass = core_module.HomeAssistant()
        entry = config_module.ConfigEntry("entry-test")
        door_entry = {"uid": "door-1", "address": "Подъезд 1"}
        hass.data.setdefault(const.DOMAIN, {})[entry.entry_id] = {
            const.DATA_COORDINATOR: SimpleNamespace(),
            const.DATA_DOOR_OPENERS: [],
            const.DATA_OPEN_DOOR: None,
            const.DATA_DOOR_STATUSES: {},
        }
        monkeypatch.setattr(button_module, "BUTTON_STATUS_RESET_DELAY_SECONDS", 0)

        error = button_module.IntersvyazApiError("boom")
        button = button_module.IntersvyazDoorOpenButton(
            hass,
            SimpleNamespace(),
            entry,
            AsyncMock(side_effect=error),
            door_entry,
        )
        button.hass = hass

        async def _scenario() -> None:
            with pytest.raises(button_module.IntersvyazApiError):
                await button.async_press()
            await asyncio.sleep(0)

        asyncio.run(_scenario())

        statuses = hass.data[const.DOMAIN][entry.entry_id][const.DATA_DOOR_STATUSES]
        stored = statuses["door-1"]
        assert stored[const.ATTR_STATUS_CODE] == const.DOOR_STATUS_READY
        assert stored[const.ATTR_STATUS_ERROR] is None
        assert button.available is True


def test_sensor_receives_status_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сенсор статуса домофона читает сохранённое состояние и реагирует на обновления."""

    with _load_modules() as modules:
        const = modules.const
        button_module = modules.button
        sensor_module = modules.sensor
        core_module = sys.modules["homeassistant.core"]
        config_module = sys.modules["homeassistant.config_entries"]
        hass = core_module.HomeAssistant()
        entry = config_module.ConfigEntry("entry-test")
        door_entry = {"uid": "door-1", "address": "Подъезд 1", "door_id": 1}
        hass.data.setdefault(const.DOMAIN, {})[entry.entry_id] = {
            const.DATA_COORDINATOR: SimpleNamespace(),
            const.DATA_DOOR_OPENERS: [door_entry],
            const.DATA_OPEN_DOOR: None,
            const.DATA_DOOR_STATUSES: {},
        }
        monkeypatch.setattr(button_module, "BUTTON_STATUS_RESET_DELAY_SECONDS", 0)

        open_mock = AsyncMock()
        button = button_module.IntersvyazDoorOpenButton(
            hass,
            SimpleNamespace(),
            entry,
            open_mock,
            door_entry,
        )
        button.hass = hass

        sensor = sensor_module.IntersvyazDoorStatusSensor(
            SimpleNamespace(data={}),
            entry,
            door_entry,
            hass.data[const.DOMAIN][entry.entry_id][const.DATA_DOOR_STATUSES],
        )
        sensor.hass = hass

        async def _scenario() -> None:
            await sensor.async_added_to_hass()
            await button.async_press()
            await asyncio.sleep(0)

        asyncio.run(_scenario())

        attrs = sensor.extra_state_attributes
        assert attrs[const.ATTR_STATUS_CODE] == const.DOOR_STATUS_READY
        assert attrs[const.ATTR_STATUS_LABEL] == const.DOOR_STATUS_LABELS[const.DOOR_STATUS_READY]
        assert attrs[const.ATTR_STATUS_UPDATED_AT] is not None
