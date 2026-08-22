"""Event entities для физических домофонов Intersvyaz."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOOR_EVENT_TYPES, SIGNAL_DOOR_EVENT
from .devices import door_device_info
from .models import DoorRuntime
from .runtime import IntersvyazConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Создать EventEntity на каждый домофон."""

    async_add_entities([IntersvyazDoorEvent(entry, door) for door in entry.runtime_data.doors])


class IntersvyazDoorEvent(EventEntity):
    """Последнее значимое событие конкретного домофона."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_event"
    _attr_event_types = list(DOOR_EVENT_TYPES)

    def __init__(self, entry: IntersvyazConfigEntry, door: DoorRuntime) -> None:
        self._entry = entry
        self._door = door
        self._attr_unique_id = f"{door.uid}_event"
        self._attr_device_info = door_device_info(entry.entry_id, door)

    async def async_added_to_hass(self) -> None:
        """Подписаться на dispatcher одного config entry."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DOOR_EVENT}_{self._entry.entry_id}",
                self._async_handle_event,
            )
        )

    @callback
    def _async_handle_event(
        self,
        door_uid: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if door_uid != self._door.uid:
            return
        attributes = {
            key: value
            for key, value in payload.items()
            if key not in {"entry_id", "door_uid", "door_name"}
        }
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
