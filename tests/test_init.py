"""Тесты для модуля инициализации интеграции Intersvyaz."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from custom_components.intersvyaz import async_setup_entry
from custom_components.intersvyaz.const import (
    CONF_BUYER_ID,
    CONF_CRM_TOKEN,
    CONF_DEVICE_ID,
    CONF_DOOR_ENTRANCE,
    CONF_DOOR_MAC,
    CONF_MOBILE_TOKEN,
    DATA_API_CLIENT,
    DATA_CONFIG,
    DATA_COORDINATOR,
    DATA_OPEN_DOOR,
    DEFAULT_BUYER_ID,
    DOMAIN,
    SERVICE_OPEN_DOOR,
)


class _DummyEntry:
    """Простейшая заглушка записи конфигурации Home Assistant."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.entry_id = "test-entry"
        self.data = data


class _DummyServices:
    """Минимальная реализация регистратора сервисов Home Assistant."""

    def __init__(self) -> None:
        self._registered: set[tuple[str, str]] = set()
        self._handlers: dict[tuple[str, str], Callable[..., Awaitable[None]]] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self._registered

    async def async_register(
        self,
        domain: str,
        service: str,
        handler: Callable[..., Awaitable[None]],
        *,
        schema: Any | None = None,
    ) -> None:
        # Регистрируем сервис и сохраняем хендлер для возможного дальнейшего использования.
        self._registered.add((domain, service))
        self._handlers[(domain, service)] = handler

    async def async_remove(self, domain: str, service: str) -> None:  # pragma: no cover - не используется
        self._registered.discard((domain, service))
        self._handlers.pop((domain, service), None)


class _DummyConfigEntries:
    """Имитация менеджера записей конфигурации."""

    def __init__(self) -> None:
        self.async_forward_entry_setups = AsyncMock()
        self.async_update_entry = AsyncMock()


class _DummyApiClient:
    """Подменный клиент API, фиксирующий обращение к методам."""

    def __init__(self, *, session: object, device_id: str, buyer_id: int) -> None:
        self.session = session
        self.device_id = device_id
        self.buyer_id = buyer_id
        self.mobile_token: SimpleNamespace | None = None
        self.crm_token: SimpleNamespace | None = None
        self.async_open_door = AsyncMock()

    def set_mobile_token(self, token: str) -> None:
        self.mobile_token = SimpleNamespace(raw=token)

    def set_crm_token(self, token: str) -> None:
        self.crm_token = SimpleNamespace(raw=token)


@pytest.mark.asyncio
async def test_async_setup_entry_registers_open_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяем, что настройка интеграции сохраняет колбэк открытия двери."""

    hass = SimpleNamespace(
        data={},
        services=_DummyServices(),
        config_entries=_DummyConfigEntries(),
    )

    # Подменяем зависимости интеграции, чтобы тест не требовал внешних библиотек.
    monkeypatch.setattr(
        "custom_components.intersvyaz.async_get_clientsession",
        lambda _hass: object(),
    )
    monkeypatch.setattr(
        "custom_components.intersvyaz.IntersvyazApiClient",
        _DummyApiClient,
    )
    fake_coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
    monkeypatch.setattr(
        "custom_components.intersvyaz.IntersvyazDataUpdateCoordinator",
        lambda hass, api_client: fake_coordinator,
    )
    persist_tokens = AsyncMock()
    monkeypatch.setattr("custom_components.intersvyaz._persist_tokens", persist_tokens)

    entry = _DummyEntry(
        {
            CONF_DEVICE_ID: "device",
            CONF_DOOR_MAC: "00:11:22:33:44:55",
            CONF_DOOR_ENTRANCE: 3,
            CONF_MOBILE_TOKEN: "mobile",
            CONF_CRM_TOKEN: "crm",
            CONF_BUYER_ID: DEFAULT_BUYER_ID,
        }
    )

    setup_result = await async_setup_entry(hass, entry)
    assert setup_result is True, "Настройка должна завершиться успехом"
    assert DOMAIN in hass.data, "Интеграция обязана создать пространство данных домена"
    stored = hass.data[DOMAIN][entry.entry_id]

    # Проверяем, что все ключевые объекты сохранены для последующего использования.
    assert DATA_API_CLIENT in stored
    assert DATA_COORDINATOR in stored
    assert DATA_CONFIG in stored
    assert callable(stored[DATA_OPEN_DOOR])

    # Сервис открытия двери должен быть зарегистрирован в Home Assistant.
    assert hass.services.has_service(DOMAIN, SERVICE_OPEN_DOOR)

    # Запуск колбэка не должен приводить к ошибке и обязан дергать API клиента.
    await stored[DATA_OPEN_DOOR]()
    api_client: _DummyApiClient = stored[DATA_API_CLIENT]
    api_client.async_open_door.assert_awaited_once_with("00:11:22:33:44:55", 3)
    persist_tokens.assert_awaited_once()
