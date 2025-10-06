"""Основной модуль интеграции Intersvyaz для Home Assistant."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IntersvyazApiClient, IntersvyazApiError, TokenInfo
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    DATA_API_CLIENT,
    DATA_CONFIG,
    DOMAIN,
    LOGGER_NAME,
    SERVICE_OPEN_DOOR,
)

_LOGGER = logging.getLogger(LOGGER_NAME)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настроить интеграцию Intersvyaz на основе записи конфигурации."""

    _LOGGER.info("Запуск настройки конфигурации entry_id=%s", entry.entry_id)

    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)
    api_client = IntersvyazApiClient(session=session)

    token_info = _create_token_info_from_entry(entry.data)
    if token_info:
        api_client.set_token_info(token_info)

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_CONFIG: dict(entry.data),
    }

    async def handle_open_door(call: ServiceCall) -> None:
        """Обработчик сервиса открытия домофона."""

        _LOGGER.info(
            "Получена команда на открытие домофона для entry_id=%s", entry.entry_id
        )
        try:
            await api_client.async_open_door()
        except IntersvyazApiError as err:
            _LOGGER.error("Не удалось открыть домофон: %s", err)
            raise
        else:
            await _sync_token_info_with_entry(hass, entry, api_client)

    hass.services.async_register(DOMAIN, SERVICE_OPEN_DOOR, handle_open_door)
    _LOGGER.info("Интеграция Intersvyaz успешно настроена")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузить конфигурацию интеграции."""

    _LOGGER.info("Выгрузка конфигурации entry_id=%s", entry.entry_id)

    hass.services.async_remove(DOMAIN, SERVICE_OPEN_DOOR)
    hass.data[DOMAIN].pop(entry.entry_id, None)

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return True


def _create_token_info_from_entry(data: Dict[str, Any]) -> TokenInfo | None:
    """Создать объект TokenInfo из данных записи конфигурации."""

    access_token = data.get(CONF_ACCESS_TOKEN)
    refresh_token = data.get(CONF_REFRESH_TOKEN)
    expires_at_str = data.get(CONF_ACCESS_TOKEN_EXPIRES_AT)

    if not access_token or not refresh_token or not expires_at_str:
        _LOGGER.debug("В записи конфигурации отсутствуют сохраненные токены")
        return None

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
    except (TypeError, ValueError) as err:
        _LOGGER.error("Не удалось разобрать время истечения токена: %s", err)
        return None

    _LOGGER.debug(
        "Восстановлены токены из конфигурации, истекают в %s",
        expires_at.isoformat(),
    )
    return TokenInfo(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


async def _sync_token_info_with_entry(
    hass: HomeAssistant, entry: ConfigEntry, api_client: IntersvyazApiClient
) -> None:
    """Сохранить обновленные токены в записи конфигурации."""

    if not api_client.token_info:
        _LOGGER.debug("Клиент API не содержит актуальной информации о токенах")
        return

    token_info = api_client.token_info
    assert token_info is not None

    _LOGGER.debug(
        "Сохранение токенов в записи конфигурации entry_id=%s", entry.entry_id
    )

    new_data = {
        **entry.data,
        CONF_ACCESS_TOKEN: token_info.access_token,
        CONF_REFRESH_TOKEN: token_info.refresh_token,
        CONF_ACCESS_TOKEN_EXPIRES_AT: token_info.expires_at.isoformat(),
    }

    hass.config_entries.async_update_entry(entry, data=new_data)

    stored_entry = hass.data[DOMAIN].get(entry.entry_id, {})
    if stored_entry:
        stored_entry[DATA_CONFIG] = new_data

    _LOGGER.debug("Токены успешно сохранены в конфигурации")
