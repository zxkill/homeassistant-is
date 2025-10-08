"""Точка входа интеграции Intersvyaz для Home Assistant."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

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
    CONF_DOOR_ADDRESS,
    CONF_DOOR_MAC,
    CONF_RELAY_NUM,
    CONF_RELAY_ID,
    CONF_MOBILE_TOKEN,
    DATA_API_CLIENT,
    DATA_CONFIG,
    DATA_COORDINATOR,
    DATA_DOOR_OPENERS,
    DATA_OPEN_DOOR,
    DATA_DOOR_STATUSES,
    DEFAULT_BUYER_ID,
    DOMAIN,
    EVENT_DOOR_OPEN_RESULT,
    LOGGER_NAME,
    SERVICE_OPEN_DOOR,
    DOOR_STATUS_READY,
    DOOR_STATUS_LABELS,
    ATTR_STATUS_CODE,
    ATTR_STATUS_LABEL,
    ATTR_STATUS_BUSY,
    ATTR_STATUS_UPDATED_AT,
    ATTR_STATUS_ERROR,
)

_LOGGER = logging.getLogger(LOGGER_NAME)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

SERVICE_OPEN_DOOR_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Optional("door_uid"): cv.string,
    }
)


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

    # Получаем перечень домофонов, доступных пользователю. Эти данные используются
    # для генерации отдельных кнопок открытия по каждому адресу.
    try:
        relays = await api_client.async_get_relays()
        _LOGGER.info(
            "Получено %s домофонов для entry_id=%s", len(relays), entry.entry_id
        )
    except IntersvyazApiError as err:
        _LOGGER.warning(
            "Не удалось обновить список домофонов при настройке entry_id=%s: %s. "
            "Будут использованы сведения из конфигурации.",
            entry.entry_id,
            err,
        )
        relays = []

    door_openers: List[Dict[str, Any]] = []
    seen_uids: set[str] = set()

    # Сортируем список так, чтобы основной подъезд всегда располагался первым,
    # а расшаренные домофоны шли далее в алфавитном порядке. Это упрощает выбор
    # «основной» кнопки и делает интерфейс предсказуемым.
    sorted_relays = sorted(
        relays,
        key=lambda relay: (
            not getattr(relay, "is_main", False),
            (relay.address or "").lower(),
        ),
    )

    for index, relay in enumerate(sorted_relays, start=1):
        mac = (
            (relay.mac or "")
            or (relay.opener.mac if getattr(relay, "opener", None) else "")
        ).strip()
        if not mac:
            _LOGGER.debug(
                "Пропускаем домофон без MAC-адреса: %s", getattr(relay, "raw", relay)
            )
            continue

        # Определяем идентификатор реле аналогично конфигурационному мастеру.
        door_id: Optional[int] = None
        opener = getattr(relay, "opener", None)
        if opener and getattr(opener, "relay_num", None) is not None:
            door_id = opener.relay_num
        elif getattr(relay, "porch_num", None):
            try:
                door_id = int(relay.porch_num)
            except (TypeError, ValueError):
                door_id = None
        if door_id is None:
            door_id = 1

        address = (relay.address or f"Домофон №{index}").strip()
        if not address:
            address = f"Домофон №{index}"

        door_uid = f"{entry.entry_id}_door_{mac.replace(':', '').lower()}_{door_id}"
        if door_uid in seen_uids:
            _LOGGER.debug(
                "Пропускаем дублирующийся домофон uid=%s (mac=%s, door_id=%s)",
                door_uid,
                mac,
                door_id,
            )
            continue

        callback = build_open_door_callable(
            hass,
            entry,
            api_client,
            mac=mac.upper(),
            door_id=int(door_id),
            address=address,
            door_uid=door_uid,
        )

        door_entry: Dict[str, Any] = {
            "uid": door_uid,
            "mac": mac.upper(),
            "door_id": int(door_id),
            "address": address,
            "is_main": bool(getattr(relay, "is_main", False)),
            "is_shared": not bool(getattr(relay, "is_main", False)),
            "relay_id": getattr(opener, "relay_id", None),
            "relay_num": getattr(opener, "relay_num", None),
            "porch_num": getattr(relay, "porch_num", None),
            "callback": callback,
        }
        seen_uids.add(door_uid)
        door_openers.append(door_entry)
        _LOGGER.debug(
            "Подготовлена кнопка домофона uid=%s: %s",
            door_uid,
            {k: v for k, v in door_entry.items() if k != "callback"},
        )

    if not door_openers:
        # В случае ошибки получения списка домофонов используем ранее сохранённую
        # информацию, чтобы пользователь не терял доступ к кнопке открытия.
        fallback_mac = str(config_data.get(CONF_DOOR_MAC, "")).upper()
        fallback_door_id = int(
            config_data.get(CONF_RELAY_NUM, config_data.get(CONF_DOOR_ENTRANCE, 1))
        )
        fallback_address = config_data.get(CONF_DOOR_ADDRESS) or "Домофон"
        fallback_uid = (
            f"{entry.entry_id}_door_{fallback_mac.replace(':', '').lower()}_{fallback_door_id}"
            if fallback_mac
            else f"{entry.entry_id}_door_fallback_{fallback_door_id}"
        )
        _LOGGER.info(
            "Используем резервные данные для кнопки домофона entry_id=%s (mac=%s, door_id=%s)",
            entry.entry_id,
            fallback_mac,
            fallback_door_id,
        )
        door_openers.append(
            {
                "uid": fallback_uid,
                "mac": fallback_mac,
                "door_id": fallback_door_id,
                "address": fallback_address,
                "is_main": True,
                "is_shared": False,
                "relay_id": config_data.get(CONF_RELAY_ID),
                "relay_num": config_data.get(CONF_RELAY_NUM),
                "porch_num": config_data.get(CONF_DOOR_ENTRANCE),
                "callback": build_open_door_callable(
                    hass,
                    entry,
                    api_client,
                    mac=fallback_mac or config_data.get(CONF_DOOR_MAC, ""),
                    door_id=fallback_door_id,
                    address=fallback_address,
                    door_uid=fallback_uid,
                ),
            }
        )

    default_entry = next(
        (door for door in door_openers if door.get("is_main")),
        door_openers[0],
    )

    # Сохраняем все вспомогательные сущности в хранилище Home Assistant, чтобы
    # сервисы и другие части интеграции могли безопасно переиспользовать их.
    # Подготавливаем словарь статусов домофонов. Он используется кнопками и
    # сенсорами для отображения прогресса открытия, поэтому заполняем его
    # начальными значениями «Готово» заранее.
    door_statuses: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for door in door_openers:
        door_uid = door.get("uid")
        if not door_uid:
            continue
        door_statuses[door_uid] = {
            ATTR_STATUS_CODE: DOOR_STATUS_READY,
            ATTR_STATUS_LABEL: DOOR_STATUS_LABELS[DOOR_STATUS_READY],
            ATTR_STATUS_BUSY: False,
            ATTR_STATUS_UPDATED_AT: now_iso,
            ATTR_STATUS_ERROR: None,
        }

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_COORDINATOR: coordinator,
        DATA_CONFIG: config_data,
        DATA_OPEN_DOOR: default_entry["callback"],
        DATA_DOOR_OPENERS: door_openers,
        DATA_DOOR_STATUSES: door_statuses,
    }

    _LOGGER.info(
        "Для entry_id=%s подготовлено %s кнопок открытия домофона (основной uid=%s)",
        entry.entry_id,
        len(door_openers),
        default_entry["uid"],
    )

    if not hass.services.has_service(DOMAIN, SERVICE_OPEN_DOOR):

        async def handle_open_door(call: ServiceCall) -> None:
            """Открыть домофон с использованием сохранённой конфигурации."""

            service_entry_id = call.data["entry_id"]
            requested_door_uid = call.data.get("door_uid")
            domain_data = hass.data.get(DOMAIN, {})
            entry_storage = domain_data.get(service_entry_id)
            if not entry_storage:
                raise HomeAssistantError(
                    f"Интеграция Intersvyaz с entry_id={service_entry_id} не найдена"
                )
            door_openers: List[Dict[str, Any]] = entry_storage.get(
                DATA_DOOR_OPENERS, []
            )
            open_door_callable: Optional[Callable[[], Awaitable[None]]]
            target_door: Optional[Dict[str, Any]] = None
            if requested_door_uid:
                target_door = next(
                    (door for door in door_openers if door.get("uid") == requested_door_uid),
                    None,
                )
                if not target_door:
                    raise HomeAssistantError(
                        f"Домофон с идентификатором {requested_door_uid} не найден для entry_id={service_entry_id}"
                    )
            elif door_openers:
                target_door = next(
                    (door for door in door_openers if door.get("is_main")),
                    door_openers[0],
                )

            if target_door:
                open_door_callable = target_door.get("callback")
                _LOGGER.info(
                    "Запрошено открытие домофона uid=%s через сервис для entry_id=%s",
                    target_door.get("uid"),
                    service_entry_id,
                )
            else:
                open_door_callable = entry_storage.get(DATA_OPEN_DOOR)

            if not callable(open_door_callable):
                raise HomeAssistantError(
                    "Сервис открытия домофона не настроен для данной записи"
                )
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


def build_open_door_callable(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api_client: IntersvyazApiClient,
    *,
    mac: str,
    door_id: int,
    address: str,
    door_uid: str,
) -> Callable[[], Awaitable[None]]:
    """Сформировать корутину, открывающую конкретный домофон.

    Функция вынесена отдельно, чтобы её можно было переиспользовать в тестах и
    сервисе `open_door`. Команда сопровождается подробным логированием и
    публикацией события о результате выполнения.
    """

    door_context = {
        "mac": mac,
        "door_id": door_id,
        "address": address,
        "door_uid": door_uid,
    }

    async def _async_open_door() -> None:
        """Выполнить команду открытия домофона с контролем результата."""

        _LOGGER.info(
            "Старт открытия домофона entry_id=%s uid=%s (mac=%s, door_id=%s, адрес=%s)",
            entry.entry_id,
            door_uid,
            mac,
            door_id,
            address,
        )
        try:
            await api_client.async_open_door(mac, door_id)
        except IntersvyazApiError as err:
            _LOGGER.error(
                "Домофон entry_id=%s uid=%s не открылся: %s",
                entry.entry_id,
                door_uid,
                err,
            )
            _fire_door_open_event(
                hass,
                entry_id=entry.entry_id,
                door_context=door_context,
                success=False,
                error=str(err),
            )
            raise

        _LOGGER.info(
            "Домофон entry_id=%s uid=%s успешно открылся (ожидался код 204)",
            entry.entry_id,
            door_uid,
        )
        _fire_door_open_event(
            hass,
            entry_id=entry.entry_id,
            door_context=door_context,
            success=True,
        )
        await _persist_tokens(hass, entry, api_client)

    return _async_open_door


def _fire_door_open_event(
    hass: HomeAssistant,
    *,
    entry_id: str,
    door_context: Dict[str, Any],
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Отправить событие Home Assistant о результате открытия домофона."""

    event_type = f"{DOMAIN}_{EVENT_DOOR_OPEN_RESULT}"
    event_payload: Dict[str, Any] = {
        "entry_id": entry_id,
        "door_uid": door_context.get("door_uid"),
        "address": door_context.get("address"),
        "mac": door_context.get("mac"),
        "door_id": door_context.get("door_id"),
        "success": success,
    }
    if error:
        event_payload["error"] = error
    _LOGGER.debug(
        "Публикуем событие %s с результатом открытия домофона: %s",
        event_type,
        event_payload,
    )
    hass.bus.async_fire(event_type, event_payload)


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
