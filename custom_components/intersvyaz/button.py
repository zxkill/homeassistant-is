"""Кнопки интеграции Intersvyaz."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import IntersvyazApiError
from .const import (
    BUTTON_STATUS_RESET_DELAY_SECONDS,
    DATA_COORDINATOR,
    DATA_DOOR_OPENERS,
    DATA_OPEN_DOOR,
    DOMAIN,
)

_LOGGER = logging.getLogger(f"{DOMAIN}.button")

# Текстовые статусы, которые видит пользователь в интерфейсе Home Assistant.
STATUS_READY = "Готово"
STATUS_OPENING = "Открываем…"
STATUS_OPENED = "Открыто"
STATUS_ERROR = "Ошибка"


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
        open_door_callable: Callable[[], Awaitable[None]],
        door_entry: dict,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        # Сохраняем вызываемый объект, который отправляет команду открытия домофона.
        self._open_door_callable = open_door_callable
        self._door_entry = door_entry
        self._attr_has_entity_name = True
        address = door_entry.get("address") or "Домофон"
        # Базовое имя выступает в роли префикса для отображения статуса.
        self._attr_name = f"Открыть домофон ({address})"
        self._base_name = self._attr_name
        self._attr_unique_id = door_entry.get(
            "uid", f"{entry.entry_id}_door_open"
        )
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        # Флаг, блокирующий повторные нажатия, пока действие выполняется или статус выводится пользователю.
        self._is_busy: bool = False
        # Храним текущий текстовый статус для отображения в интерфейсе.
        self._status: str = STATUS_READY
        # Асинхронная задача, которая сбрасывает статус после небольшого ожидания.
        self._status_reset_task: Optional[asyncio.Task[None]] = None
        # Изначально кнопка доступна к нажатию, а атрибуты отражают базовое состояние.
        self._attr_available = True
        self._attr_extra_state_attributes = self._compose_state_attributes()

    @property
    def name(self) -> str | None:
        """Вернуть имя кнопки с учётом динамического статуса."""

        if self._status == STATUS_READY:
            return self._base_name
        # Добавляем понятную подсказку: «Открыть домофон (Адрес) — Открыто/Ошибка/…».
        return f"{self._base_name} — {self._status}"

    @property
    def state(self) -> str | None:
        """Показываем текущий статус в столбце состояния карточки."""

        return self._status

    def _compose_state_attributes(self) -> dict[str, Optional[str] | bool]:
        """Сформировать словарь атрибутов с контекстом домофона и статусом."""

        return {
            "door_uid": self._door_entry.get("uid"),
            "door_address": self._door_entry.get("address"),
            "door_mac": self._door_entry.get("mac"),
            "door_id": self._door_entry.get("door_id"),
            "status": self._status,
            "busy": self._is_busy,
        }

    async def async_press(self) -> None:
        """Отправить команду на открытие домофона."""

        if self._is_busy:
            # Если предыдущий статус ещё отображается, игнорируем повторный клик и пишем в лог.
            _LOGGER.debug(
                "Пропускаем повторное нажатие кнопки entry_id=%s uid=%s: статус ещё отображается",
                self._entry.entry_id,
                self._door_entry.get("uid"),
            )
            return

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
        # Переводим кнопку в «занято» и уведомляем пользователя о попытке открытия.
        self._cancel_status_reset()
        self._set_status(STATUS_OPENING, busy=True)
        try:
            await self._open_door_callable()
        except IntersvyazApiError as err:
            _LOGGER.error(
                "Ошибка при открытии домофона entry_id=%s uid=%s: %s",
                self._entry.entry_id,
                self._door_entry.get("uid"),
                err,
            )
            # Показываем ошибку пользователю и планируем возврат к исходному состоянию.
            self._set_status(STATUS_ERROR, busy=True)
            self._schedule_status_reset()
            raise
        except Exception as err:  # pragma: no cover - неожиданные ошибки логируем подробно
            _LOGGER.exception(
                "Непредвиденная ошибка при открытии домофона entry_id=%s uid=%s: %s",
                self._entry.entry_id,
                self._door_entry.get("uid"),
                err,
            )
            self._set_status(STATUS_ERROR, busy=True)
            self._schedule_status_reset()
            raise
        _LOGGER.info(
            "Команда открытия домофона entry_id=%s uid=%s завершилась успешно",
            self._entry.entry_id,
            self._door_entry.get("uid"),
        )
        # Сообщаем об успешном открытии и возвращаем кнопку в исходное состояние чуть позже.
        self._set_status(STATUS_OPENED, busy=True)
        self._schedule_status_reset()

    async def async_will_remove_from_hass(self) -> None:
        """Отменить отложенные задачи перед удалением сущности."""

        self._cancel_status_reset(release_busy=True)
        # Возвращаем кнопку в исходное состояние, чтобы при следующем добавлении не мигал старый статус.
        self._set_status(STATUS_READY, busy=False)

    def _set_status(self, status: str, *, busy: Optional[bool] = None) -> None:
        """Обновить текущий статус кнопки и синхронизировать его с интерфейсом."""

        if busy is not None:
            self._is_busy = busy
        self._status = status
        _LOGGER.debug(
            "Обновляем статус кнопки entry_id=%s uid=%s: статус=%s, занятость=%s",
            self._entry.entry_id,
            self._door_entry.get("uid"),
            status,
            self._is_busy,
        )
        # Имя кнопки дополняем текущим статусом, чтобы пользователь видел результат прямо на панели.
        if self._status == STATUS_READY:
            self._attr_name = self._base_name
        else:
            self._attr_name = f"{self._base_name} — {self._status}"
        # Пока статус показывается, блокируем повторное нажатие.
        self._attr_available = not self._is_busy
        self._attr_extra_state_attributes = self._compose_state_attributes()
        self.async_write_ha_state()

    def _schedule_status_reset(self) -> None:
        """Запланировать возврат кнопки к исходному виду после таймаута."""

        self._cancel_status_reset()

        async def _async_reset_after_delay() -> None:
            """Подождать таймаут и снять статус занятости."""

            cancelled = False
            try:
                await asyncio.sleep(BUTTON_STATUS_RESET_DELAY_SECONDS)
            except asyncio.CancelledError:
                _LOGGER.debug(
                    "Сброс статуса кнопки entry_id=%s uid=%s отменён",
                    self._entry.entry_id,
                    self._door_entry.get("uid"),
                )
                cancelled = True
                return
            finally:
                if cancelled:
                    return
                # Если таймер завершился без отмены, разблокируем кнопку.
                self._set_status(STATUS_READY, busy=False)
            self._status_reset_task = None

        # Создаём задачу сброса в текущем цикле событий.
        loop = asyncio.get_running_loop()
        self._status_reset_task = loop.create_task(_async_reset_after_delay())

    def _cancel_status_reset(self, *, release_busy: bool = False) -> None:
        """Отменить запланированный сброс статуса и при необходимости разблокировать кнопку."""

        if self._status_reset_task and not self._status_reset_task.done():
            self._status_reset_task.cancel()
        self._status_reset_task = None
        if release_busy:
            self._is_busy = False
            self._attr_available = True
