"""Кнопки интеграции Intersvyaz."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import IntersvyazApiError
from .const import DATA_COORDINATOR, DATA_DOOR_OPENERS, DATA_OPEN_DOOR, DOMAIN

_LOGGER = logging.getLogger(f"{DOMAIN}.button")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Создать кнопку открытия домофона."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]
    door_entries = entry_data.get(DATA_DOOR_OPENERS, [])

    if not door_entries:
        _LOGGER.warning(
            "Для entry_id=%s не найден список домофонов, будет создана резервная кнопка",
            entry.entry_id,
        )
        door_entries = [
            {
                "uid": f"{entry.entry_id}_door_legacy",
                "address": "Домофон",
                "callback": entry_data.get(DATA_OPEN_DOOR),
                "mac": None,
                "door_id": None,
                "is_main": True,
                "is_shared": False,
            }
        ]

    buttons: list[IntersvyazDoorOpenButton] = []
    for door_entry in door_entries:
        callback = door_entry.get("callback") or entry_data.get(DATA_OPEN_DOOR)
        if not callable(callback):
            _LOGGER.debug(
                "Пропускаем домофон без вызываемого обработчика: %s",
                {k: v for k, v in door_entry.items() if k != "callback"},
            )
            continue
        buttons.append(
            IntersvyazDoorOpenButton(
                coordinator,
                entry,
                callback,
                door_entry,
            )
        )

    _LOGGER.info(
        "Добавляем %s кнопок открытия домофона для entry_id=%s",
        len(buttons),
        entry.entry_id,
    )
    async_add_entities(buttons)


class IntersvyazDoorOpenButton(CoordinatorEntity, ButtonEntity):
    """Кнопка, которая инициирует открытие домофона через облако."""

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        open_door_callable,
        door_entry: dict,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._open_door_callable = open_door_callable
        self._door_entry = door_entry
        self._attr_has_entity_name = True
        address = door_entry.get("address") or "Домофон"
        self._attr_name = f"Открыть домофон ({address})"
        self._attr_unique_id = door_entry.get(
            "uid", f"{entry.entry_id}_door_open"
        )
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        """Отправить команду на открытие домофона."""

        door_context = {
            "uid": self._door_entry.get("uid"),
            "address": self._door_entry.get("address"),
            "mac": self._door_entry.get("mac"),
            "door_id": self._door_entry.get("door_id"),
        }
        _LOGGER.info(
            "Нажата кнопка открытия домофона для entry_id=%s: %s",
            self._entry.entry_id,
            door_context,
        )
        try:
            await self._open_door_callable()
        except IntersvyazApiError as err:
            _LOGGER.error(
                "Ошибка при открытии домофона entry_id=%s uid=%s: %s",
                self._entry.entry_id,
                self._door_entry.get("uid"),
                err,
            )
            raise
        _LOGGER.info(
            "Команда открытия домофона entry_id=%s uid=%s завершилась успешно",
            self._entry.entry_id,
            self._door_entry.get("uid"),
        )
