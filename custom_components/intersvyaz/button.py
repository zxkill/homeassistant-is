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
from .const import DATA_COORDINATOR, DATA_OPEN_DOOR, DOMAIN

_LOGGER = logging.getLogger(f"{DOMAIN}.button")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Создать кнопку открытия домофона."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]
    async_add_entities([
        IntersvyazDoorOpenButton(coordinator, entry, entry_data[DATA_OPEN_DOOR])
    ])


class IntersvyazDoorOpenButton(CoordinatorEntity, ButtonEntity):
    """Кнопка, которая инициирует открытие домофона через облако."""

    def __init__(self, coordinator, entry: ConfigEntry, open_door_callable) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._open_door_callable = open_door_callable
        self._attr_has_entity_name = True
        self._attr_name = "Открыть домофон"
        self._attr_unique_id = f"{entry.entry_id}_door_open"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        """Отправить команду на открытие домофона."""

        _LOGGER.info("Нажата кнопка открытия домофона для entry_id=%s", self._entry.entry_id)
        try:
            await self._open_door_callable()
        except IntersvyazApiError as err:
            _LOGGER.error("Ошибка при открытии домофона: %s", err)
            raise
