"""Базовые заглушки Home Assistant и voluptuous для запуска тестов без зависимостей."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import timedelta
from typing import Any, Callable, Iterable, Mapping, TypeVar, Generic


def _ensure_module(name: str) -> types.ModuleType:
    """Создать или вернуть уже загруженный модуль по указанному имени."""

    if name in sys.modules:
        return sys.modules[name]  # type: ignore[return-value]
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# --- voluptuous ---

if "voluptuous" in sys.modules and not hasattr(sys.modules["voluptuous"], "Schema"):
    del sys.modules["voluptuous"]

if importlib.util.find_spec("voluptuous") is None and "voluptuous" not in sys.modules:
    vol = types.ModuleType("voluptuous")

    class Schema:
        def __init__(self, schema: Any) -> None:
            self.schema = schema

        def __call__(self, value: Any) -> Any:
            return value

    class _Marker:
        def __init__(self, key: Any, default: Any = None) -> None:
            self.key = key
            self.default = default

    def Required(key: Any, default: Any = None) -> _Marker:
        return _Marker(key, default)

    def Optional(key: Any, default: Any = None) -> _Marker:  # pragma: no cover - запасная ветка
        return _Marker(key, default)

    vol.Schema = Schema  # type: ignore[attr-defined]
    vol.Required = Required  # type: ignore[attr-defined]
    vol.Optional = Optional  # type: ignore[attr-defined]
    sys.modules["voluptuous"] = vol


# --- homeassistant базовый пакет ---

ha = _ensure_module("homeassistant")


# --- aiohttp ---

if importlib.util.find_spec("aiohttp") is None:
    aiohttp_module = _ensure_module("aiohttp")

    class ClientError(Exception):
        pass

    class ClientResponse:  # pragma: no cover - минимальная заглушка
        pass

    class ClientSession:  # pragma: no cover - минимальная заглушка
        async def get(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("ClientSession.get must be patched in tests")

    aiohttp_module.ClientError = ClientError  # type: ignore[attr-defined]
    aiohttp_module.ClientResponse = ClientResponse  # type: ignore[attr-defined]
    aiohttp_module.ClientSession = ClientSession  # type: ignore[attr-defined]
    aiohttp_module.__spec__ = types.SimpleNamespace()  # type: ignore[attr-defined]
    sys.modules.setdefault("aiohttp.pytest_plugin", types.ModuleType("aiohttp.pytest_plugin"))


# --- homeassistant базовый пакет ---

ha = _ensure_module("homeassistant")


# homeassistant.const
const_module = _ensure_module("homeassistant.const")

class Platform:  # pragma: no cover - используется только как контейнер констант
    SENSOR = "sensor"
    BUTTON = "button"
    CAMERA = "camera"


const_module.Platform = Platform  # type: ignore[attr-defined]
ha.const = const_module  # type: ignore[attr-defined]


# homeassistant.exceptions
exceptions_module = _ensure_module("homeassistant.exceptions")

class HomeAssistantError(Exception):
    pass


exceptions_module.HomeAssistantError = HomeAssistantError  # type: ignore[attr-defined]
ha.exceptions = exceptions_module  # type: ignore[attr-defined]


# homeassistant.core
core_module = _ensure_module("homeassistant.core")


class ServiceCall:
    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        self.data = dict(data or {})


class HomeAssistant:  # pragma: no cover - минимальная реализация
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.services = types.SimpleNamespace(
            has_service=lambda *_: False,
            async_register=lambda *_, **__: None,
            async_remove=lambda *_, **__: None,
        )
        self.config_entries = types.SimpleNamespace(
            async_update_entry=lambda *_, **__: None,
            async_forward_entry_setups=lambda *_, **__: None,
        )

    async def async_add_executor_job(self, func: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


core_module.HomeAssistant = HomeAssistant  # type: ignore[attr-defined]
core_module.ServiceCall = ServiceCall  # type: ignore[attr-defined]


def callback(func: Callable[..., Any]) -> Callable[..., Any]:  # pragma: no cover
    return func


core_module.callback = callback  # type: ignore[attr-defined]
ha.core = core_module  # type: ignore[attr-defined]


# homeassistant.config_entries
config_entries_module = _ensure_module("homeassistant.config_entries")


class ConfigEntry:  # pragma: no cover - для типизации в тестах
    def __init__(self, entry_id: str = "entry", data: Mapping[str, Any] | None = None) -> None:
        self.entry_id = entry_id
        self.data = dict(data or {})
        self.options: dict[str, Any] = {}

    def async_on_unload(self, func: Callable[[], Any]) -> Callable[[], Any]:
        return func

    def add_update_listener(self, listener: Callable[[HomeAssistant, "ConfigEntry"], Any]):
        def _remove() -> None:
            return None

        self._update_listener = listener  # type: ignore[attr-defined]
        return _remove


class ConfigFlow:  # pragma: no cover
    DOMAIN: str | None = None

    def __init_subclass__(cls, *, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.DOMAIN = domain

    async def async_set_unique_id(self, *_: Any, **__: Any) -> None:
        return None


class OptionsFlow:  # pragma: no cover
    pass


config_entries_module.ConfigEntry = ConfigEntry  # type: ignore[attr-defined]
config_entries_module.ConfigFlow = ConfigFlow  # type: ignore[attr-defined]
config_entries_module.OptionsFlow = OptionsFlow  # type: ignore[attr-defined]
ha.config_entries = config_entries_module  # type: ignore[attr-defined]


# homeassistant.components.camera
camera_module = _ensure_module("homeassistant.components.camera")


class Camera:  # pragma: no cover - базовый класс без логики
    def __init__(self) -> None:
        pass


camera_module.Camera = Camera  # type: ignore[attr-defined]
ha.components = types.SimpleNamespace(camera=camera_module)  # type: ignore[attr-defined]


# homeassistant.helpers
helpers_module = _ensure_module("homeassistant.helpers")

# config_validation подмодуль
cv_module = _ensure_module("homeassistant.helpers.config_validation")


def string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    return value


def multi_select(options: Mapping[str, Any] | Iterable[str]) -> Callable[[Iterable[str]], list[str]]:
    valid = set(options if isinstance(options, Mapping) else list(options))

    def _validator(values: Iterable[str]) -> list[str]:
        return [value for value in values if value in valid]

    return _validator


cv_module.string = string  # type: ignore[attr-defined]
cv_module.multi_select = multi_select  # type: ignore[attr-defined]
helpers_module.config_validation = cv_module  # type: ignore[attr-defined]


# aiohttp_client подмодуль
aiohttp_client_module = _ensure_module("homeassistant.helpers.aiohttp_client")


async def async_get_clientsession(_hass: Any) -> Any:  # pragma: no cover - в тестах подменяется
    return types.SimpleNamespace()


aiohttp_client_module.async_get_clientsession = async_get_clientsession  # type: ignore[attr-defined]
helpers_module.aiohttp_client = aiohttp_client_module  # type: ignore[attr-defined]


# event подмодуль
event_module = _ensure_module("homeassistant.helpers.event")


def async_track_time_interval(
    _hass: Any, action: Callable[[Any], Any], interval: timedelta
) -> Callable[[], None]:
    def _cancel() -> None:
        return None

    return _cancel


event_module.async_track_time_interval = async_track_time_interval  # type: ignore[attr-defined]
helpers_module.event = event_module  # type: ignore[attr-defined]


# device_registry подмодуль
device_registry_module = _ensure_module("homeassistant.helpers.device_registry")


class DeviceInfo(dict):  # pragma: no cover - достаточно поведения dict
    pass


device_registry_module.DeviceInfo = DeviceInfo  # type: ignore[attr-defined]
helpers_module.device_registry = device_registry_module  # type: ignore[attr-defined]


# entity_platform подмодуль
entity_platform_module = _ensure_module("homeassistant.helpers.entity_platform")
entity_platform_module.AddEntitiesCallback = Callable[[Iterable[Any]], None]  # type: ignore[attr-defined]
helpers_module.entity_platform = entity_platform_module  # type: ignore[attr-defined]


# update_coordinator подмодуль
update_coordinator_module = _ensure_module("homeassistant.helpers.update_coordinator")


class UpdateFailed(Exception):
    pass


_T = TypeVar("_T")


class DataUpdateCoordinator(Generic[_T]):  # pragma: no cover - используется в моках
    def __init__(self, hass: HomeAssistant, logger: Any, name: str, update_interval: timedelta) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval

    async def async_config_entry_first_refresh(self) -> None:
        return None


update_coordinator_module.DataUpdateCoordinator = DataUpdateCoordinator  # type: ignore[attr-defined]
update_coordinator_module.UpdateFailed = UpdateFailed  # type: ignore[attr-defined]
helpers_module.update_coordinator = update_coordinator_module  # type: ignore[attr-defined]


# selector подмодуль
selector_module = _ensure_module("homeassistant.helpers.selector")


class FileSelectorConfig:  # pragma: no cover - хранит параметры селектора
    def __init__(self, *, accept: list[str] | None = None, multiple: bool = False) -> None:
        self.accept = accept or []
        self.multiple = multiple


class FileSelector:  # pragma: no cover - совместим с интерфейсом HA
    def __init__(self, config: FileSelectorConfig) -> None:
        self.config = config

    def __voluptuous_compile__(self, _: Any) -> Callable[[Any, Any], Any]:
        return lambda _path, value: value


selector_module.FileSelectorConfig = FileSelectorConfig  # type: ignore[attr-defined]
selector_module.FileSelector = FileSelector  # type: ignore[attr-defined]
helpers_module.selector = selector_module  # type: ignore[attr-defined]


ha.helpers = helpers_module  # type: ignore[attr-defined]
