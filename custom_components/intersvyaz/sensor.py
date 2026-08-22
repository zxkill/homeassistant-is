"""Sensor entities аккаунта и домофонов Intersvyaz."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOOR_EVENT_FACE_RECOGNIZED,
    DOOR_EVENT_UNKNOWN_PERSON,
    SIGNAL_DOOR_EVENT,
)
from .devices import account_device_info, door_device_info
from .models import DoorRuntime
from .runtime import IntersvyazConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IntersvyazConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        IntersvyazBalanceSensor(coordinator, entry),
        IntersvyazProfileSensor(coordinator, entry),
    ]
    for door in entry.runtime_data.doors:
        entities.append(IntersvyazDoorStatusSensor(entry, door))
        entities.append(IntersvyazLastVisitorSensor(entry, door))
    async_add_entities(entities)


class _AccountSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: IntersvyazConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = account_device_info(entry.entry_id)


class IntersvyazBalanceSensor(_AccountSensor):
    """Баланс договора."""

    _attr_translation_key = "balance"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "RUB"

    def __init__(self, coordinator, entry: IntersvyazConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_balance"

    @property
    def native_value(self) -> float | None:
        payload = (self.coordinator.data or {}).get("balance", {})
        value = payload.get("balance") if isinstance(payload, dict) else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = (self.coordinator.data or {}).get("balance", {})
        if not isinstance(payload, dict):
            return {}
        blocked = payload.get("blocked")
        if not isinstance(blocked, dict):
            blocked = {}
        return {
            "debt": payload.get("debt"),
            "next_payment": payload.get("nextPayment"),
            "lock_text": blocked.get("text"),
            "lock_pay": blocked.get("pay"),
        }


class IntersvyazProfileSensor(_AccountSensor):
    """Краткий профиль абонента без credentials."""

    _attr_translation_key = "profile"

    def __init__(self, coordinator, entry: IntersvyazConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_profile"

    @property
    def native_value(self) -> str | None:
        payload = (self.coordinator.data or {}).get("user", {})
        if not isinstance(payload, dict):
            return None
        return payload.get("profileName") or payload.get("shortFio") or payload.get("FULL_NAME")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = (self.coordinator.data or {}).get("user", {})
        if not isinstance(payload, dict):
            return {}
        # Телефон намеренно не дублируется в state attributes.
        return {
            "account": payload.get("ACCOUNT_NUM"),
            "role": payload.get("roleName"),
            "services": payload.get("uslugaList"),
        }


class IntersvyazDoorStatusSensor(SensorEntity):
    """Статус, который провайдер сообщает для конкретного домофона."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: IntersvyazConfigEntry, door: DoorRuntime) -> None:
        self._entry = entry
        self._door_uid = door.uid
        self._attr_unique_id = f"{door.uid}_status"
        self._attr_device_info = door_device_info(entry.entry_id, door)

    @property
    def native_value(self) -> str | None:
        door = self._entry.runtime_data.door_manager.get(self._door_uid)
        if door is None:
            return None
        return door.status_text or door.status_code or "Доступен"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        door = self._entry.runtime_data.door_manager.get(self._door_uid)
        if door is None:
            return {}
        return {
            "is_main": door.is_main,
            "is_shared": door.is_shared,
            "has_video": door.has_video,
            "status_code": door.status_code,
        }


class IntersvyazLastVisitorSensor(SensorEntity):
    """Последний распознанный/неизвестный посетитель домофона."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_visitor"
    _attr_icon = "mdi:account-eye"

    def __init__(self, entry: IntersvyazConfigEntry, door: DoorRuntime) -> None:
        self._entry = entry
        self._door_uid = door.uid
        self._attr_unique_id = f"{door.uid}_last_visitor"
        self._attr_device_info = door_device_info(entry.entry_id, door)
        self._attr_native_value: str | None = None
        self._event_attributes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        last = self._entry.runtime_data.last_visitors.get(self._door_uid)
        if last:
            self._apply_payload(last.get("event_type"), last)
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
        if door_uid != self._door_uid:
            return
        if event_type not in (DOOR_EVENT_FACE_RECOGNIZED, DOOR_EVENT_UNKNOWN_PERSON):
            return
        self._apply_payload(event_type, payload)
        self.async_write_ha_state()

    def _apply_payload(self, event_type: str | None, payload: dict[str, Any]) -> None:
        if event_type == DOOR_EVENT_FACE_RECOGNIZED:
            self._attr_native_value = str(payload.get("person") or "Известный")
        elif event_type == DOOR_EVENT_UNKNOWN_PERSON:
            self._attr_native_value = "Неизвестный"
        self._event_attributes = {
            key: payload.get(key)
            for key in (
                "distance",
                "match_score",
                "faces_detected",
                "confirmed",
                "streak",
                "required_matches",
            )
            if key in payload
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._event_attributes)
