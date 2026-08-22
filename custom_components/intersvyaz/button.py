"""Button entities открытия домофонов Intersvyaz."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .devices import door_device_info
from .models import DoorRuntime
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.button")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    buttons = [IntersvyazDoorOpenButton(entry, door) for door in entry.runtime_data.doors]
    _LOGGER.info("Создаём кнопки открытия: entry_id=%s count=%s", entry.entry_id, len(buttons))
    async_add_entities(buttons)


class IntersvyazDoorOpenButton(ButtonEntity):
    """Открывает конкретный физический домофон."""

    _attr_has_entity_name = True
    _attr_translation_key = "open_door"

    def __init__(self, entry: IntersvyazConfigEntry, door: DoorRuntime) -> None:
        self._entry = entry
        self._door_uid = door.uid
        self._attr_unique_id = door.uid
        self._attr_device_info = door_device_info(entry.entry_id, door)
        self._attr_icon = "mdi:door-open"

    async def async_press(self) -> None:
        door = self._entry.runtime_data.door_manager.get(self._door_uid)
        if door is None or not callable(door.callback):
            raise HomeAssistantError("Домофон больше недоступен")
        _LOGGER.info(
            "Нажата кнопка открытия: entry_id=%s door=%s",
            self._entry.entry_id,
            safe_door_ref(door.uid),
        )
        await door.callback()
