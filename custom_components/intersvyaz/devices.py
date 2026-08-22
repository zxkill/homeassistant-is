"""DeviceInfo helpers для корректной иерархии устройств Home Assistant."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .models import DoorRuntime


def account_device_info(entry_id: str, *, name: str = "Intersvyaz") -> DeviceInfo:
    """Виртуальный hub/аккаунт Интерсвязи."""

    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=name,
        manufacturer='АО "Интерсвязь"',
        model="Cloud account",
        entry_type=DeviceEntryType.SERVICE,
    )


def door_device_info(entry_id: str, door: DoorRuntime) -> DeviceInfo:
    """Отдельное устройство для физического домофона."""

    name = door.address.strip() if door.address else "Intercom"
    return DeviceInfo(
        identifiers={(DOMAIN, door.uid)},
        name=f"Intersvyaz — {name}",
        manufacturer='АО "Интерсвязь"',
        model="Smart intercom",
        via_device=(DOMAIN, entry_id),
    )
