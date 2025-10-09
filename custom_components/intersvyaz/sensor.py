"""Сенсоры интеграции Intersvyaz."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant import const as ha_const
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DATA_CONFIG, DOMAIN

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
        # Home Assistant запрещает сочетать денежный класс с state_class
        # "measurement", поэтому явно обнуляем state_class. Так мы избегаем
        # предупреждений регистратора и подчеркиваем, что значение баланса
        # может как уменьшаться, так и увеличиваться.
        self._attr_state_class = None
        # Используем ранее вычисленную единицу измерения, чтобы корректно
        # отображать валюту в UI независимо от версии Home Assistant.
        self._attr_native_unit_of_measurement = BALANCE_UNIT

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
