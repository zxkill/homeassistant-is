"""Event entities для домофонов и camera-only устройств Intersvyaz."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOOR_EVENT_TYPES, SIGNAL_DOOR_EVENT
from .devices import door_device_info, yard_camera_device_info
from .models import DoorRuntime, YardCameraRuntime
from .runtime import IntersvyazConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[EventEntity] = [
        IntersvyazDoorEvent(entry, door) for door in entry.runtime_data.doors
    ]
    entities.extend(
        IntersvyazYardCameraEvent(entry, camera)
        for camera in entry.runtime_data.live_yard_cameras
        if not camera.matched_door_uid
    )
    async_add_entities(entities)


class _IntersvyazEventBase(EventEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "door_event"
    _attr_event_types = list(DOOR_EVENT_TYPES)

    def __init__(self, entry: IntersvyazConfigEntry, source_uid: str) -> None:
        self._entry = entry
        self._source_uid = source_uid

    async def async_added_to_hass(self) -> None:
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
        if door_uid != self._source_uid:
            return
        attributes = {
            key: value
            for key, value in payload.items()
            if key not in {"entry_id", "door_uid", "door_name"}
        }
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()


class IntersvyazDoorEvent(_IntersvyazEventBase):
    def __init__(self, entry: IntersvyazConfigEntry, door: DoorRuntime) -> None:
        super().__init__(entry, door.uid)
        self._attr_unique_id = f"{door.uid}_event"
        self._attr_device_info = door_device_info(entry.entry_id, door)


class IntersvyazYardCameraEvent(_IntersvyazEventBase):
    def __init__(self, entry: IntersvyazConfigEntry, camera: YardCameraRuntime) -> None:
        super().__init__(entry, camera.uid)
        self._attr_unique_id = f"{camera.uid}_event"
        self._attr_device_info = yard_camera_device_info(entry.entry_id, camera)
