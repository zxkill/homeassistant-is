"""Сенсоры интеграции Intersvyaz."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant import const as ha_const
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_STATUS_BUSY,
    ATTR_STATUS_CODE,
    ATTR_STATUS_ERROR,
    ATTR_STATUS_LABEL,
    ATTR_STATUS_UPDATED_AT,
    DATA_CONFIG,
    DATA_COORDINATOR,
    DATA_DOOR_OPENERS,
    DATA_DOOR_STATUSES,
    DOMAIN,
    DOOR_STATUS_LABELS,
    DOOR_STATUS_READY,
    SIGNAL_DOOR_STATUS_UPDATED,
)

_LOGGER = logging.getLogger(f"{DOMAIN}.sensor")


def _resolve_balance_unit() -> Any:
    """Определить единицу измерения баланса с учетом версии Home Assistant."""

    # В новых релизах Home Assistant денежные единицы представлены перечислением
    # UnitOfCurrency. Мы используем его при наличии, чтобы сохранить типобезопасность
    # и корректное отображение в интерфейсе.
    if hasattr(ha_const, "UnitOfCurrency"):
        ruble_unit = ha_const.UnitOfCurrency.RUBLE
        _LOGGER.debug(
            "Используем перечисление UnitOfCurrency.RUBLE для отображения баланса",
        )
        return ruble_unit

    # Старые релизы предоставляют строковую константу CURRENCY_RUB. Если её нет,
    # подставляем код RUB, чтобы не допустить падение интеграции и сохранить
    # читабельный вывод в интерфейсе.
    legacy_unit = getattr(ha_const, "CURRENCY_RUB", "RUB")
    _LOGGER.debug(
        "Используем строковую единицу измерения %s из homeassistant.const",
        legacy_unit,
    )
    return legacy_unit


# Фиксируем выбранную единицу измерения один раз при импортировании модуля, чтобы
# сенсоры не выполняли повторных проверок и логов при каждом обновлении.
BALANCE_UNIT = _resolve_balance_unit()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Создать сенсоры после настройки конфигурации."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]

    sensors: list[SensorEntity] = [
        IntersvyazBalanceSensor(coordinator, entry),
        IntersvyazProfileSensor(coordinator, entry),
    ]
    door_entries = entry_data.get(DATA_DOOR_OPENERS, [])
    door_statuses = entry_data.get(DATA_DOOR_STATUSES, {})
    for door_entry in door_entries:
        sensors.append(
            IntersvyazDoorStatusSensor(
                coordinator,
                entry,
                door_entry,
                door_statuses,
            )
        )
    _LOGGER.debug(
        "Добавляем %d сенсора для записи %s: %s",
        len(sensors),
        entry.entry_id,
        [sensor.__class__.__name__ for sensor in sensors],
    )
    async_add_entities(sensors, update_before_add=True)


class IntersvyazBaseSensor(CoordinatorEntity, SensorEntity):
    """Базовый класс с общими удобствами."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_has_entity_name = True
        self._attr_device_info = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        """Создать объект DeviceInfo для группировки сущностей."""

        entry_data = self.coordinator.data or {}
        user = entry_data.get("user", {})
        identifier = (DOMAIN, self._entry.entry_id)
        manufacturer = user.get("firm", {}).get("NAME", "АО \"Интерсвязь\"")
        model = user.get("roleName", "Профиль абонента")
        return DeviceInfo(
            identifiers={identifier},
            name=user.get("profileName") or user.get("FULL_NAME") or "Интерсвязь",
            manufacturer=manufacturer,
            model=model,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Вернуть дополнительные атрибуты по умолчанию."""

        return {}


class IntersvyazBalanceSensor(IntersvyazBaseSensor):
    """Сенсор, отображающий баланс договора."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Баланс"
        self._attr_unique_id = f"{entry.entry_id}_balance"
        self._attr_device_class = SensorDeviceClass.MONETARY
        # Параметр state_class намеренно обнуляем: Home Assistant запрещает
        # комбинацию `device_class=monetary` и `state_class=measurement`, что
        # приводило к предупреждению в логах. Нам важно избежать ложных
        # сообщений, поэтому явно оставляем значение `None` и фиксируем это в
        # отладочном сообщении.
        self._attr_state_class = None
        # Используем ранее вычисленную единицу измерения, чтобы корректно
        # отображать валюту в UI независимо от версии Home Assistant.
        self._attr_native_unit_of_measurement = BALANCE_UNIT
        _LOGGER.debug(
            "Сенсор баланса создан без state_class для совместимости с Home Assistant",
        )

    @property
    def native_value(self) -> float | None:
        """Вернуть текущий баланс в виде числа."""

        balance_payload = (self.coordinator.data or {}).get("balance", {})
        balance_raw = balance_payload.get("balance")
        if balance_raw is None:
            return None
        try:
            return float(balance_raw)
        except (TypeError, ValueError):
            _LOGGER.debug("Не удалось преобразовать баланс %s к числу", balance_raw)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Вернуть дополнительные атрибуты, включая блокировки."""

        balance_payload = (self.coordinator.data or {}).get("balance", {})
        blocked = balance_payload.get("blocked") or {}
        attributes: dict[str, Any] = {
            "debt": balance_payload.get("debt"),
            "next_payment": balance_payload.get("nextPayment"),
            "lock_text": blocked.get("text"),
            "lock_pay": blocked.get("pay"),
        }
        return attributes


class IntersvyazProfileSensor(IntersvyazBaseSensor):
    """Сенсор, отображающий основные данные профиля."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Профиль"
        self._attr_unique_id = f"{entry.entry_id}_profile"

    @property
    def native_value(self) -> str | None:
        """Вернуть краткое имя профиля."""

        user_payload = (self.coordinator.data or {}).get("user", {})
        return (
            user_payload.get("profileName")
            or user_payload.get("shortFio")
            or user_payload.get("FULL_NAME")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Вернуть расширенные атрибуты профиля."""

        data = self.coordinator.data or {}
        user_payload = data.get("user", {})
        config = self.hass.data[DOMAIN][self._entry.entry_id][DATA_CONFIG]
        attributes: dict[str, Any] = {
            "login": user_payload.get("LOGIN"),
            "account": user_payload.get("ACCOUNT_NUM"),
            "phone": user_payload.get("PHONE") or config.get("phone_number"),
            "role": user_payload.get("roleName"),
            "services": user_payload.get("uslugaList"),
        }
        return attributes


class IntersvyazDoorStatusSensor(IntersvyazBaseSensor):
    """Диагностический сенсор, отображающий последний статус открытия домофона."""

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        door_entry: dict[str, Any],
        door_statuses: dict[str, dict[str, Any]],
    ) -> None:
        super().__init__(coordinator, entry)
        self._door_entry = door_entry
        self._door_statuses = door_statuses
        self._door_uid = door_entry.get("uid") or f"{entry.entry_id}_door_unknown"
        address = door_entry.get("address") or "Домофон"
        self._attr_name = f"Статус домофона ({address})"
        self._attr_unique_id = f"{self._door_uid}_status"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        # Начальные значения совпадают с логикой кнопки: статус «Готово» без ошибок.
        self._status_code: str = DOOR_STATUS_READY
        self._status_label: str = DOOR_STATUS_LABELS[DOOR_STATUS_READY]
        self._status_busy: bool = False
        self._status_updated_at: Optional[str] = None
        self._last_error: Optional[str] = None
        # При инициализации подгружаем сохранённое состояние из общего хранилища,
        # чтобы после перезапуска Home Assistant пользователь сразу видел последний результат нажатия.
        self._apply_payload(self._door_statuses.get(self._door_uid))

    async def async_added_to_hass(self) -> None:
        """Подписаться на обновления статуса от кнопки."""

        await super().async_added_to_hass()

        def _handle_status_update(entry_id: str, door_uid: str, payload: dict[str, Any]) -> None:
            """Обработать изменение статуса конкретного домофона."""

            if entry_id != self._entry.entry_id or door_uid != self._door_uid:
                return
            _LOGGER.debug(
                "Сенсор статуса домофона uid=%s получил обновление: %s",
                door_uid,
                payload,
            )
            self._apply_payload(payload)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_DOOR_STATUS_UPDATED,
                _handle_status_update,
            )
        )

    @property
    def native_value(self) -> Optional[str]:
        """Вернуть последний человекочитаемый статус."""

        return self._status_label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Включить в атрибуты технический контекст домофона и метаданные."""

        return {
            "door_uid": self._door_uid,
            "door_address": self._door_entry.get("address"),
            "door_mac": self._door_entry.get("mac"),
            "door_id": self._door_entry.get("door_id"),
            ATTR_STATUS_CODE: self._status_code,
            ATTR_STATUS_LABEL: self._status_label,
            ATTR_STATUS_BUSY: self._status_busy,
            ATTR_STATUS_UPDATED_AT: self._status_updated_at,
            ATTR_STATUS_ERROR: self._last_error,
        }

    def _apply_payload(self, payload: Optional[dict[str, Any]]) -> None:
        """Синхронизировать внутреннее состояние сенсора с переданным словарём."""

        if not payload:
            return
        # Берём машинный код статуса и вычисляем человекочитаемую подпись через общий словарь.
        status_code = payload.get(ATTR_STATUS_CODE, DOOR_STATUS_READY)
        self._status_code = status_code
        self._status_label = DOOR_STATUS_LABELS.get(
            status_code, payload.get(ATTR_STATUS_LABEL, DOOR_STATUS_LABELS[DOOR_STATUS_READY])
        )
        self._status_busy = bool(payload.get(ATTR_STATUS_BUSY))
        self._status_updated_at = payload.get(ATTR_STATUS_UPDATED_AT)
        self._last_error = payload.get(ATTR_STATUS_ERROR)
