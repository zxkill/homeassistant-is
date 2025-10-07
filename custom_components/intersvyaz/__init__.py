"""Точка входа интеграции Intersvyaz для Home Assistant."""
from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IntersvyazApiClient, IntersvyazApiError
from .coordinator import IntersvyazDataUpdateCoordinator
from .const import (
    CONF_BUYER_ID,
    CONF_CRM_TOKEN,
    CONF_DEVICE_ID,
    CONF_DOOR_ENTRANCE,
    CONF_DOOR_MAC,
    CONF_RELAY_NUM,
    CONF_MOBILE_TOKEN,
    DATA_API_CLIENT,
    DATA_CONFIG,
    DATA_COORDINATOR,
    DATA_OPEN_DOOR,
    DEFAULT_BUYER_ID,
    DOMAIN,
    LOGGER_NAME,
    SERVICE_OPEN_DOOR,
)

_LOGGER = logging.getLogger(LOGGER_NAME)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

SERVICE_OPEN_DOOR_SCHEMA = vol.Schema({vol.Required("entry_id"): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настроить интеграцию Intersvyaz на основе записи конфигурации."""

    _LOGGER.info("Запуск настройки entry_id=%s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})

    config_data = dict(entry.data)
    session = async_get_clientsession(hass)
    buyer_id = int(config_data.get(CONF_BUYER_ID, DEFAULT_BUYER_ID))

    api_client = IntersvyazApiClient(
        session=session,
        device_id=config_data.get(CONF_DEVICE_ID),
        buyer_id=buyer_id,
    )

    if CONF_MOBILE_TOKEN in config_data:
        api_client.set_mobile_token(config_data[CONF_MOBILE_TOKEN])
    if CONF_CRM_TOKEN in config_data:
        api_client.set_crm_token(config_data[CONF_CRM_TOKEN])

    coordinator = IntersvyazDataUpdateCoordinator(hass, api_client)
    await coordinator.async_config_entry_first_refresh()

    async def _async_open_door() -> None:
        """Асинхронно открыть домофон, используя сохранённую конфигурацию."""

        mac = config_data[CONF_DOOR_MAC]
        door_id = int(config_data.get(CONF_RELAY_NUM, config_data[CONF_DOOR_ENTRANCE]))
        _LOGGER.info(
            "Выполняем команду открытия домофона для entry_id=%s (mac=%s, door_id=%s)",
            entry.entry_id,
            mac,
            door_id,
        )
        await api_client.async_open_door(mac, door_id)
        await _persist_tokens(hass, entry, api_client)

    # Сохраняем все вспомогательные сущности в хранилище Home Assistant, чтобы
    # сервисы и другие части интеграции могли безопасно переиспользовать их.
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_COORDINATOR: coordinator,
        DATA_CONFIG: config_data,
        DATA_OPEN_DOOR: _async_open_door,
    }

    if not hass.services.has_service(DOMAIN, SERVICE_OPEN_DOOR):

        async def handle_open_door(call: ServiceCall) -> None:
            """Открыть домофон с использованием сохранённой конфигурации."""

            service_entry_id = call.data["entry_id"]
            domain_data = hass.data.get(DOMAIN, {})
            entry_storage = domain_data.get(service_entry_id)
            if not entry_storage:
                raise HomeAssistantError(
                    f"Интеграция Intersvyaz с entry_id={service_entry_id} не найдена"
                )
            open_door_callable = entry_storage[DATA_OPEN_DOOR]
            try:
                await open_door_callable()
            except IntersvyazApiError as err:
                _LOGGER.error("Не удалось открыть домофон: %s", err)
                raise HomeAssistantError(str(err)) from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_OPEN_DOOR,
            handle_open_door,
            schema=SERVICE_OPEN_DOOR_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info("Интеграция Intersvyaz успешно настроена")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузить конфигурацию интеграции."""

    _LOGGER.info("Выгрузка entry_id=%s", entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain_store = hass.data.get(DOMAIN, {})
    domain_store.pop(entry.entry_id, None)
    if not domain_store:
        hass.services.async_remove(DOMAIN, SERVICE_OPEN_DOOR)
        hass.data.pop(DOMAIN, None)

    return unload_ok


async def _persist_tokens(
    hass: HomeAssistant, entry: ConfigEntry, api_client: IntersvyazApiClient
) -> None:
    """Сохранить обновлённые токены в записи конфигурации."""

    domain_store = hass.data.get(DOMAIN)
    if not domain_store or entry.entry_id not in domain_store:
        _LOGGER.debug(
            "Запрошено сохранение токенов, но запись entry_id=%s не найдена", entry.entry_id
        )
        return
    stored = domain_store[entry.entry_id]
    config_data: Dict[str, Any] = dict(stored.get(DATA_CONFIG, {}))

    if api_client.mobile_token:
        config_data[CONF_MOBILE_TOKEN] = api_client.mobile_token.raw
    if api_client.crm_token:
        config_data[CONF_CRM_TOKEN] = api_client.crm_token.raw

    if config_data != entry.data:
        _LOGGER.debug("Обнаружены обновления токенов, сохраняем в конфигурации")
        hass.config_entries.async_update_entry(entry, data=config_data)
        stored[DATA_CONFIG] = config_data
