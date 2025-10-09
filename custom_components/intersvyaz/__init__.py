"""Точка входа интеграции Intersvyaz для Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import IntersvyazApiClient, IntersvyazApiError, RelayInfo
from .coordinator import IntersvyazDataUpdateCoordinator
from .const import (
    CONF_BUYER_ID,
    CONF_CRM_TOKEN,
    CONF_DEVICE_ID,
    CONF_DOOR_ENTRANCE,
    CONF_DOOR_ADDRESS,
    CONF_DOOR_HAS_VIDEO,
    CONF_DOOR_IMAGE_URL,
    CONF_DOOR_MAC,
    CONF_DOOR_OPEN_LINK,
    CONF_RELAY_NUM,
    CONF_RELAY_ID,
    CONF_MOBILE_TOKEN,
    DATA_API_CLIENT,
    DATA_CONFIG,
    DATA_COORDINATOR,
    DATA_DOOR_OPENERS,
    DATA_DOOR_REFRESH_UNSUB,
    DATA_OPEN_DOOR,
    DEFAULT_BUYER_ID,
    DOOR_LINK_REFRESH_INTERVAL_HOURS,
    DOMAIN,
    LOGGER_NAME,
    SERVICE_OPEN_DOOR,
)

_LOGGER = logging.getLogger(LOGGER_NAME)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON, Platform.CAMERA]

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
    # для генерации отдельных кнопок открытия по каждому адресу и для камер.
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

    def _make_open_callable(door_entry: Dict[str, Any]) -> Callable[[], Awaitable[None]]:
        """Создать корутину для открытия конкретного домофона."""

        async def _async_open_door() -> None:
            """Выполнить команду открытия с подробным логированием."""

            mac = door_entry.get("mac")
            door_id = door_entry.get("door_id")
            address = door_entry.get("address")
            open_link = door_entry.get("open_link")
            _LOGGER.info(
                "Выполняем команду открытия домофона entry_id=%s uid=%s "
                "(mac=%s, door_id=%s, адрес=%s, open_link=%s)",
                entry.entry_id,
                door_entry.get("uid"),
                mac,
                door_id,
                address,
                open_link,
            )
            await api_client.async_open_door(
                mac,
                door_id,
                open_link=open_link,
            )
            await _persist_tokens(hass, entry, api_client)

        return _async_open_door

    door_openers: List[Dict[str, Any]] = []
    seen_uids: set[str] = set()

    for index, relay in enumerate(_sort_relays(relays), start=1):
        door_payload = _build_door_entry_payload(entry.entry_id, relay, index)
        if not door_payload:
            continue

        door_uid = door_payload["uid"]
        if door_uid in seen_uids:
            _LOGGER.debug(
                "Пропускаем дублирующийся домофон uid=%s", door_uid
            )
            continue

        door_entry = dict(door_payload)
        door_entry["callback"] = _make_open_callable(door_entry)
        door_openers.append(door_entry)
        seen_uids.add(door_uid)
        _LOGGER.debug(
            "Подготовлена кнопка домофона uid=%s: %s",
            door_uid,
            {k: v for k, v in door_entry.items() if k != "callback"},
        )

    if not door_openers:
        # В случае ошибки получения списка домофонов используем ранее сохранённую
        # информацию, чтобы пользователь не терял доступ к кнопке открытия.
        fallback_mac = str(config_data.get(CONF_DOOR_MAC, "") or "").upper()
        fallback_door_id = int(
            config_data.get(CONF_RELAY_NUM, config_data.get(CONF_DOOR_ENTRANCE, 1))
        )
        fallback_address = config_data.get(CONF_DOOR_ADDRESS) or "Домофон"
        fallback_open_link = config_data.get(CONF_DOOR_OPEN_LINK)
        fallback_image = config_data.get(CONF_DOOR_IMAGE_URL)
        fallback_has_video = bool(config_data.get(CONF_DOOR_HAS_VIDEO))
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
        fallback_entry: Dict[str, Any] = {
            "uid": fallback_uid,
            "mac": fallback_mac,
            "door_id": fallback_door_id,
            "address": fallback_address,
            "is_main": True,
            "is_shared": False,
            "relay_id": config_data.get(CONF_RELAY_ID),
            "relay_num": config_data.get(CONF_RELAY_NUM),
            "porch_num": config_data.get(CONF_DOOR_ENTRANCE),
            "open_link": fallback_open_link,
            "image_url": fallback_image,
            "has_video": fallback_has_video,
        }
        fallback_entry["callback"] = _make_open_callable(fallback_entry)
        door_openers.append(fallback_entry)

    default_entry = next(
        (door for door in door_openers if door.get("is_main")),
        door_openers[0],
    )

    # Сохраняем все вспомогательные сущности в хранилище Home Assistant, чтобы
    # сервисы и другие части интеграции могли безопасно переиспользовать их.
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_COORDINATOR: coordinator,
        DATA_CONFIG: config_data,
        DATA_OPEN_DOOR: default_entry["callback"],
        DATA_DOOR_OPENERS: door_openers,
        DATA_DOOR_REFRESH_UNSUB: None,
    }

    _sync_config_with_primary_door(hass, entry, door_openers)

    async def _scheduled_refresh(_now=None) -> None:
        """Периодически обновлять ссылки открытия и снимки домофонов."""

        await _async_refresh_door_links(hass, entry, api_client)

    refresh_interval = timedelta(hours=DOOR_LINK_REFRESH_INTERVAL_HOURS)
    hass.data[DOMAIN][entry.entry_id][DATA_DOOR_REFRESH_UNSUB] = (
        async_track_time_interval(hass, _scheduled_refresh, refresh_interval)
    )
    _LOGGER.info(
        "Плановое обновление ссылок домофонов для entry_id=%s будет выполняться каждые %s часов",
        entry.entry_id,
        DOOR_LINK_REFRESH_INTERVAL_HOURS,
    )

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
    entry_store = domain_store.pop(entry.entry_id, None)
    if entry_store:
        unsubscribe = entry_store.get(DATA_DOOR_REFRESH_UNSUB)
        if callable(unsubscribe):
            unsubscribe()
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


def _sort_relays(relays: Iterable[RelayInfo]) -> List[RelayInfo]:
    """Отсортировать список домофонов: основной подъезд сверху."""

    return sorted(
        relays,
        key=lambda relay: (
            not getattr(relay, "is_main", False),
            (getattr(relay, "address", "") or "").lower(),
        ),
    )


def _build_door_entry_payload(
    entry_id: str, relay: RelayInfo, index: int
) -> Optional[Dict[str, Any]]:
    """Преобразовать структуру RelayInfo в словарь с данными домофона."""

    opener = getattr(relay, "opener", None)
    mac_candidate = (
        (relay.mac or "")
        or (opener.mac if opener and getattr(opener, "mac", None) else "")
    ).strip()
    if not mac_candidate:
        _LOGGER.debug(
            "Пропускаем домофон без MAC-адреса при подготовке кнопок: %s",
            getattr(relay, "raw", relay),
        )
        return None

    door_id: Optional[int] = None
    if opener and getattr(opener, "relay_num", None) is not None:
        door_id = opener.relay_num
    elif getattr(relay, "porch_num", None):
        try:
            door_id = int(relay.porch_num)
        except (TypeError, ValueError):
            door_id = None
    if door_id is None:
        door_id = 1

    mac_normalized = mac_candidate.upper()
    door_uid = f"{entry_id}_door_{mac_normalized.replace(':', '').lower()}_{door_id}"
    address = (getattr(relay, "address", "") or f"Домофон №{index}").strip()
    if not address:
        address = f"Домофон №{index}"

    open_link = getattr(relay, "open_link", None)
    if isinstance(open_link, str):
        open_link = open_link.strip() or None
    image_url = getattr(relay, "image_url", None)
    if isinstance(image_url, str):
        image_url = image_url.strip() or None

    door_entry: Dict[str, Any] = {
        "uid": door_uid,
        "mac": mac_normalized,
        "door_id": int(door_id),
        "address": address,
        "is_main": bool(getattr(relay, "is_main", False)),
        "is_shared": not bool(getattr(relay, "is_main", False)),
        "relay_id": getattr(opener, "relay_id", None),
        "relay_num": getattr(opener, "relay_num", None),
        "porch_num": getattr(relay, "porch_num", None),
        "open_link": open_link,
        "image_url": image_url,
        "has_video": bool(getattr(relay, "has_video", False)),
    }
    return door_entry


def _sync_config_with_primary_door(
    hass: HomeAssistant, entry: ConfigEntry, door_openers: Iterable[Dict[str, Any]]
) -> None:
    """Синхронизировать конфигурацию с актуальным основным домофоном."""

    domain_store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not domain_store:
        _LOGGER.debug(
            "Запрошено обновление конфигурации домофона, но запись entry_id=%s не найдена",
            entry.entry_id,
        )
        return

    config_data: Dict[str, Any] = dict(domain_store.get(DATA_CONFIG, entry.data))
    primary = next((door for door in door_openers if door.get("is_main")), None)
    if not primary:
        primary = next(iter(door_openers), None)
    if not primary:
        _LOGGER.debug(
            "Не удалось найти данные домофона для синхронизации конфигурации entry_id=%s",
            entry.entry_id,
        )
        return

    updates = {
        CONF_DOOR_MAC: primary.get("mac"),
        CONF_RELAY_NUM: primary.get("door_id"),
        CONF_DOOR_ADDRESS: primary.get("address"),
        CONF_DOOR_ENTRANCE: primary.get("porch_num"),
        CONF_RELAY_ID: primary.get("relay_id"),
        CONF_DOOR_HAS_VIDEO: primary.get("has_video"),
        CONF_DOOR_IMAGE_URL: primary.get("image_url"),
        CONF_DOOR_OPEN_LINK: primary.get("open_link"),
    }

    changed = False
    for key, value in updates.items():
        if config_data.get(key) != value:
            config_data[key] = value
            changed = True

    if changed:
        _LOGGER.debug(
            "Обновляем сохранённую конфигурацию entry_id=%s актуальными ссылками и адресом",
            entry.entry_id,
        )
        hass.config_entries.async_update_entry(entry, data=config_data)
        domain_store[DATA_CONFIG] = config_data


async def _async_refresh_door_links(
    hass: HomeAssistant, entry: ConfigEntry, api_client: IntersvyazApiClient
) -> None:
    """Перезапросить список домофонов и обновить ссылки открытия/снимков."""

    _LOGGER.info(
        "Запускаем плановое обновление ссылок домофонов для entry_id=%s",
        entry.entry_id,
    )
    try:
        relays = await api_client.async_get_relays()
    except IntersvyazApiError as err:
        _LOGGER.warning(
            "Не удалось обновить ссылки домофонов entry_id=%s: %s",
            entry.entry_id,
            err,
        )
        return

    domain_store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not domain_store:
        _LOGGER.debug(
            "Запрошено обновление ссылок, но запись entry_id=%s не найдена",
            entry.entry_id,
        )
        return

    door_openers: List[Dict[str, Any]] = domain_store.get(DATA_DOOR_OPENERS, [])
    if not door_openers:
        _LOGGER.debug(
            "В списке entry_id=%s отсутствуют домофоны для обновления ссылок",
            entry.entry_id,
        )
        return

    existing_by_uid = {
        door.get("uid"): door for door in door_openers if door.get("uid")
    }

    for index, relay in enumerate(_sort_relays(relays), start=1):
        payload = _build_door_entry_payload(entry.entry_id, relay, index)
        if not payload:
            continue
        uid = payload["uid"]
        door_entry = existing_by_uid.get(uid)
        if not door_entry:
            _LOGGER.info(
                "Обнаружен новый домофон uid=%s для entry_id=%s. Перезапустите "
                "интеграцию для создания дополнительных сущностей.",
                uid,
                entry.entry_id,
            )
            continue
        for key, value in payload.items():
            if key == "uid":
                continue
            door_entry[key] = value
        _LOGGER.debug(
            "Обновлены данные домофона uid=%s: open_link=%s image_url=%s",
            uid,
            door_entry.get("open_link"),
            door_entry.get("image_url"),
        )

    _sync_config_with_primary_door(hass, entry, door_openers)
    primary = next((door for door in door_openers if door.get("is_main")), None)
    if not primary and door_openers:
        primary = door_openers[0]
    if primary and callable(primary.get("callback")):
        domain_store[DATA_OPEN_DOOR] = primary["callback"]
