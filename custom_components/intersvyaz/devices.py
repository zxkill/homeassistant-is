"""DeviceInfo helpers для корректной иерархии устройств Home Assistant."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .models import DoorRuntime, YardCameraRuntime

# entry_id -> device_id устройства-аккаунта.
# С HA 2026.9 `via_device` (кортеж идентификаторов) устарел: сущности, которые
# добавляются из фоновой задачи (камеры двора), HA отклоняет с RuntimeError.
# Родителя указываем через `via_device_id`, для этого аккаунт регистрируется заранее.
_HUB_DEVICE_IDS: dict[str, str] = {}


def register_hub_device(hass: HomeAssistant, entry_id: str) -> None:
    """Создать устройство-аккаунт до платформ и запомнить его device_id."""

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id, **account_device_info(entry_id)
    )
    _HUB_DEVICE_IDS[entry_id] = device.id


def forget_hub_device(entry_id: str) -> None:
    """Забыть device_id аккаунта при выгрузке записи."""

    _HUB_DEVICE_IDS.pop(entry_id, None)


def _via_hub(entry_id: str) -> DeviceInfo:
    device_id = _HUB_DEVICE_IDS.get(entry_id)
    return DeviceInfo(via_device_id=device_id) if device_id else DeviceInfo()


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
        **_via_hub(entry_id),
    )


def yard_camera_device_info(entry_id: str, camera: YardCameraRuntime) -> DeviceInfo:
    """Отдельное camera-only устройство, если у камеры нет relay-домофона."""

    name = camera.address.strip() or camera.name.strip() or "Камера Интерсвязи"
    return DeviceInfo(
        identifiers={(DOMAIN, camera.uid)},
        name=f"Intersvyaz — {name}",
        manufacturer='АО "Интерсвязь"',
        model="Yard camera",
        **_via_hub(entry_id),
    )
